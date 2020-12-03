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

from typing import List

import aws_cdk.aws_ec2 as ec2
import aws_cdk.core as core

from datamaker_cli.manifest import Manifest
from datamaker_cli.manifest.subnet import SubnetKind, SubnetManifest
from datamaker_cli.manifest.team import TeamManifest


class Ec2Builder:
    @staticmethod
    def build_subnets_from_kind(
        scope: core.Construct, subnet_manifests: List[SubnetManifest], subnet_kind: SubnetKind
    ) -> List[ec2.ISubnet]:
        return [
            ec2.PrivateSubnet.from_subnet_attributes(
                scope=scope,
                id=s.subnet_id,
                subnet_id=s.subnet_id,
                availability_zone=s.availability_zone,
                route_table_id=s.route_table_id,
            )
            for s in subnet_manifests
            if s.kind == subnet_kind
        ]

    @staticmethod
    def build_efs_security_group(
        scope: core.Construct, manifest: Manifest, team_manifest: TeamManifest, vpc: ec2.Vpc, subnet_kind: SubnetKind
    ) -> ec2.SecurityGroup:
        name: str = f"datamaker-{manifest.name}-{team_manifest.name}-efs-sg"
        sg = ec2.SecurityGroup(
            scope=scope,
            id=name,
            security_group_name=name,
            vpc=vpc,
            allow_all_outbound=True,
        )

        for subnet in manifest.vpc.subnets:
            if subnet.kind is subnet_kind:
                sg.add_ingress_rule(
                    peer=ec2.Peer.ipv4(subnet.cidr_block),
                    connection=ec2.Port.tcp(port=2049),
                    description=f"Allowing internal access from subnet {subnet.subnet_id}.",
                )
        core.Tags.of(scope=sg).add(key="Name", value=name)
        return sg