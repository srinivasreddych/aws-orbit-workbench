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

import logging

from datamaker_cli import bundle, plugins, remote
from datamaker_cli.changeset import Changeset, extract_changeset
from datamaker_cli.manifest import Manifest
from datamaker_cli.messages import MessagesContext
from datamaker_cli.services import cfn, codebuild

_logger: logging.Logger = logging.getLogger(__name__)


def destroy_image(filename: str, name: str, debug: bool) -> None:
    with MessagesContext("Destroying Docker Image", debug=debug) as ctx:
        manifest = Manifest(filename=filename)
        manifest.fillup()
        ctx.info(f"Manifest loaded: {filename}")
        if cfn.does_stack_exist(manifest=manifest, stack_name=f"datamaker-{manifest.name}") is False:
            ctx.error("Please, deploy your environment before deploy/destroy any docker image")
            return

        _logger.debug("Inspecting possible manifest changes...")
        changes: Changeset = extract_changeset(manifest=manifest, ctx=ctx)
        _logger.debug(f"Changeset: {changes.asdict()}")
        ctx.progress(2)

        plugins.PLUGINS_REGISTRIES.load_plugins(manifest=manifest, ctx=ctx, changes=changes.plugin_changesets)
        ctx.progress(3)

        bundle_path = bundle.generate_bundle(
            command_name=f"destroy_image-{name}", manifest=manifest, dirs=[], changeset=changes
        )
        ctx.progress(4)
        buildspec = codebuild.generate_spec(
            manifest=manifest,
            plugins=False,
            cmds_build=[f"datamaker remote --command destroy_image {name}"],
            changeset=changes,
        )
        remote.run(
            command_name=f"destroy_image-{name}",
            manifest=manifest,
            bundle_path=bundle_path,
            buildspec=buildspec,
            codebuild_log_callback=ctx.progress_bar_callback,
            timeout=10,
        )
        ctx.info("Docker Image destroyed from ECR")
        ctx.progress(100)