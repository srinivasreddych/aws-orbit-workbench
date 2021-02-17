#  Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License").
#    You may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import json
import logging
import os
import shutil
import sys
from typing import TYPE_CHECKING, List, Optional, cast

import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_efs as efs
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_ssm as ssm
import aws_cdk.core as core
from aws_cdk.core import App, Construct, Environment, IConstruct, Stack, Tags

from aws_orbit.models.changeset import load_changeset_from_ssm
from aws_orbit.models.context import load_context_from_ssm
from aws_orbit.models.manifest import load_manifest_from_ssm
from aws_orbit.remote_files.cdk.team_builders._lambda import LambdaBuilder
from aws_orbit.remote_files.cdk.team_builders.ec2 import Ec2Builder
from aws_orbit.remote_files.cdk.team_builders.ecs import EcsBuilder
from aws_orbit.remote_files.cdk.team_builders.efs import EfsBuilder
from aws_orbit.remote_files.cdk.team_builders.iam import IamBuilder
from aws_orbit.remote_files.cdk.team_builders.stepfunctions import StateMachineBuilder

if TYPE_CHECKING:
    from aws_orbit.models.changeset import Changeset
    from aws_orbit.models.context import Context, TeamContext
    from aws_orbit.models.manifest import Manifest, TeamManifest

_logger: logging.Logger = logging.getLogger(__name__)


class Team(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        context: "Context",
        team_name: str,
        team_policies: List[str],
        image: Optional[str],
    ) -> None:
        self.scope = scope
        self.id = id
        self.context: "Context" = context
        self.team_name: str = team_name
        self.team_policies: List[str] = team_policies
        self.image: Optional[str] = image
        super().__init__(
            scope=scope,
            id=id,
            stack_name=id,
            env=Environment(account=self.context.account_id, region=self.context.region),
        )
        Tags.of(scope=cast(IConstruct, self)).add(key="Env", value=f"orbit-{self.context.name}")
        Tags.of(scope=cast(IConstruct, self)).add(key="TeamSpace", value=self.team_name)

        if self.context.networking.vpc_id is None:
            raise ValueError("self.context.networking.vpc_id is None!")
        self.i_vpc = ec2.Vpc.from_vpc_attributes(
            scope=self,
            id="vpc",
            vpc_id=self.context.networking.vpc_id,
            availability_zones=cast(List[str], self.context.networking.availability_zones),
        )
        self.i_isolated_subnets = Ec2Builder.build_subnets(
            scope=self, subnet_manifests=context.networking.isolated_subnets
        )
        self.i_private_subnets = Ec2Builder.build_subnets(
            scope=self, subnet_manifests=context.networking.private_subnets
        )
        administrator_arns: List[str] = []  # A place to add other admins if needed for KMS
        admin_principals = iam.CompositePrincipal(
            *[iam.ArnPrincipal(arn) for arn in administrator_arns],
            iam.ArnPrincipal(f"arn:aws:iam::{self.context.account_id}:root"),
        )
        self.team_kms_key: kms.Key = kms.Key(
            self,
            id="kms-key",
            removal_policy=core.RemovalPolicy.RETAIN,
            enabled=True,
            enable_key_rotation=True,
            policy=iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW, actions=["kms:*"], resources=["*"], principals=[admin_principals]
                    )
                ]
            ),
        )
        self.team_security_group: ec2.SecurityGroup = Ec2Builder.build_team_security_group(
            scope=self, context=context, team_name=self.team_name, vpc=self.i_vpc
        )
        self.ecr_repo: ecr.Repository = ecr.Repository(
            scope=self,
            id="repo",
            repository_name=f"orbit-{self.context.name}-{self.team_name}",
        )

        self.ecr_repo_spark: ecr.Repository = ecr.Repository(
            scope=self,
            id="repo-spark",
            repository_name=f"orbit-{self.context.name}-{self.team_name}-spark",
        )

        self.policies: List[str] = self.team_policies
        if self.context.scratch_bucket_arn:
            self.scratch_bucket: s3.Bucket = cast(
                s3.Bucket,
                s3.Bucket.from_bucket_attributes(
                    scope=self,
                    id="scratch_bucket",
                    bucket_arn=self.context.scratch_bucket_arn,
                    bucket_name=self.context.scratch_bucket_arn.split(":::")[1],
                ),
            )
        else:
            raise Exception("Scratch bucket was not provided in Manifest ('ScratchBucketArn')")

        self.role_eks_pod = IamBuilder.build_team_role(
            scope=self,
            context=self.context,
            team_name=self.team_name,
            policy_names=self.policies,
            scratch_bucket=self.scratch_bucket,
            team_kms_key=self.team_kms_key,
        )
        shared_fs_name: str = f"orbit-{context.name}-{self.team_name}-shared-fs"
        if context.shared_efs_fs_id is None:
            raise Exception("Shared EFS File system ID was not provided in Manifest ('SharedEfsFsId')")

        if context.shared_efs_sg_id is None:
            raise Exception("Shared EFS File system security group ID was not provided in Manifest ('SharedEfsSgId')")

        self.shared_fs: efs.FileSystem = cast(
            efs.FileSystem,
            efs.FileSystem.from_file_system_attributes(
                scope=self,
                id=shared_fs_name,
                file_system_id=context.shared_efs_fs_id,
                security_group=ec2.SecurityGroup.from_security_group_id(
                    scope=self, id="team_sec_group", security_group_id=context.shared_efs_sg_id
                ),
            ),
        )

        self.efs_ap: efs.AccessPoint = EfsBuilder.build_file_system_access_point(
            scope=self, team_name=team_name, shared_fs=self.shared_fs
        )

        self.ecs_cluster = EcsBuilder.build_cluster(scope=self, context=context, team_name=team_name, vpc=self.i_vpc)
        self.ecr_image = EcsBuilder.build_ecr_image(scope=self, context=context, image=self.image)
        self.ecr_image_spark = EcsBuilder.build_ecr_image_spark(scope=self, context=context, image=self.image)
        self.ecs_execution_role = IamBuilder.build_ecs_role(scope=self)
        self.ecs_task_definition = EcsBuilder.build_task_definition(
            scope=self,
            context=context,
            team_name=team_name,
            ecs_execution_role=self.ecs_execution_role,
            ecs_task_role=self.role_eks_pod,
            file_system=self.shared_fs,
            fs_accesspoint=self.efs_ap,
            image=self.ecr_image,
        )
        self.container_runner_role = IamBuilder.build_container_runner_role(
            scope=self, context=context, team_name=team_name
        )
        self.ecs_fargate_runner = StateMachineBuilder.build_ecs_run_container_state_machine(
            scope=self,
            context=context,
            team_name=team_name,
            cluster=self.ecs_cluster,
            task_definition=self.ecs_task_definition,
            team_security_group=self.team_security_group,
            subnets=self.i_private_subnets
            if self.context.networking.data.internet_accessible
            else self.i_isolated_subnets,
            role=self.container_runner_role,
            scratch_bucket=self.scratch_bucket,
        )
        self.eks_k8s_api = StateMachineBuilder.build_eks_k8s_api_state_machine(
            scope=self,
            context=context,
            team_name=team_name,
            role=self.container_runner_role,
        )
        self.eks_fargate_runner = StateMachineBuilder.build_eks_run_container_state_machine(
            scope=self,
            context=context,
            team_name=team_name,
            image=self.ecr_image,
            role=self.container_runner_role,
            node_type="fargate",
        )
        self.eks_ec2_runner = StateMachineBuilder.build_eks_run_container_state_machine(
            scope=self,
            context=context,
            team_name=team_name,
            image=self.ecr_image,
            role=self.container_runner_role,
            node_type="ec2",
        )

        self.container_runner = LambdaBuilder.build_container_runner(
            scope=self,
            context=context,
            team_name=team_name,
            ecs_fargate_runner=self.ecs_fargate_runner,
            eks_fargate_runner=self.eks_fargate_runner,
            eks_ec2_runner=self.eks_ec2_runner,
        )
        team_ssm_parameter_name: str = f"/orbit/{context.name}/teams/{self.team_name}/team"
        self.context_parameter: ssm.StringParameter = ssm.StringParameter(
            scope=self,
            id=team_ssm_parameter_name,
            string_value=json.dumps(
                {
                    "EfsId": self.shared_fs.file_system_id,
                    "EfsApId": self.efs_ap.access_point_id,
                    "EksPodRoleArn": self.role_eks_pod.role_arn,
                    "ScratchBucket": self.scratch_bucket.bucket_name,
                    "EcsClusterName": self.ecs_cluster.cluster_name,
                    "ContainerRunnerArn": self.container_runner.function_arn,
                    "EksK8sApiArn": self.eks_k8s_api.state_machine_arn,
                    "TeamKmsKeyArn": self.team_kms_key.key_arn,
                    "TeamSecurityGroupId": self.team_security_group.security_group_id,
                }
            ),
            type=ssm.ParameterType.STRING,
            description="Orbit Workbench Team Context.",
            parameter_name=team_ssm_parameter_name,
            simple_name=False,
            tier=ssm.ParameterTier.INTELLIGENT_TIERING,
        )
        ssm_profile_name = f"/orbit/{self.context.name}/teams/{self.team_name}/user/profiles"
        self.user_profiles: ssm.StringParameter = ssm.StringParameter(
            scope=self,
            id=ssm_profile_name,
            string_value="[]",
            type=ssm.ParameterType.STRING,
            description="Team additional profiles created by the team users",
            parameter_name=ssm_profile_name,
            simple_name=False,
            tier=ssm.ParameterTier.INTELLIGENT_TIERING,
        )


def main() -> None:
    _logger.debug("sys.argv: %s", sys.argv)
    if len(sys.argv) == 3:
        context: "Context" = load_context_from_ssm(env_name=sys.argv[1])
        team_name: str = sys.argv[2]
    else:
        raise ValueError("Unexpected number of values in sys.argv.")

    changeset: Optional["Changeset"] = load_changeset_from_ssm(env_name=context.name)
    _logger.debug("Changeset loaded.")

    team_policies: Optional[List[str]] = None
    image: Optional[str] = None

    if changeset and changeset.teams_changeset and team_name in changeset.teams_changeset.added_teams_names:
        manifest: Optional["Manifest"] = load_manifest_from_ssm(env_name=sys.argv[1])
        if manifest is None:
            raise ValueError("manifest is None!")
        team_manifest: Optional["TeamManifest"] = manifest.get_team_by_name(name=team_name)
        if team_manifest:
            team_policies = team_manifest.policies
            image = team_manifest.image
        else:
            raise ValueError(f"{team_name} not found in manifest!")
    else:
        team_context: Optional["TeamContext"] = context.get_team_by_name(name=team_name)
        if team_context:
            team_policies = team_context.policies
            image = team_context.image
        else:
            raise ValueError(f"Team {team_name} not found in the context.")

    if team_policies is None:
        raise ValueError("team_policies is None!")

    stack_name: str = f"orbit-{context.name}-{team_name}"
    outdir = os.path.join(".orbit.out", context.name, "cdk", stack_name)
    os.makedirs(outdir, exist_ok=True)
    shutil.rmtree(outdir)
    app = App(outdir=outdir)
    Team(scope=app, id=stack_name, context=context, team_name=team_name, team_policies=team_policies, image=image)
    app.synth(force=True)


if __name__ == "__main__":
    main()
