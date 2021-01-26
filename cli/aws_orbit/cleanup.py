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

import concurrent.futures
import logging
import pprint
import time
from itertools import repeat
from typing import Any, Dict, List, Optional, cast

import botocore.exceptions
from aws_orbit.manifest import Manifest
from aws_orbit.services import elb

_logger: logging.Logger = logging.getLogger(__name__)


def _detach_network_interface(nid: int, network_interface: Any) -> None:
    _logger.debug(f"Detaching NetworkInterface: {nid}.")
    network_interface.detach()
    _logger.debug(f"Reloading NetworkInterface: {nid}.")
    network_interface.reload()


def _network_interface(manifest: Manifest, vpc_id: str) -> None:
    client = manifest.boto3_client("ec2")
    ec2 = manifest.boto3_resource("ec2")
    for i in client.describe_network_interfaces(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["NetworkInterfaces"]:
        try:
            network_interface = ec2.NetworkInterface(i["NetworkInterfaceId"])
            if "Interface for NAT Gateway" not in network_interface.description:
                _logger.debug(f"Forgotten NetworkInterface: {i['NetworkInterfaceId']}.")
                if network_interface.attachment is not None and network_interface.attachment["Status"] == "attached":
                    attempts: int = 0
                    while network_interface.attachment is None or network_interface.attachment["Status"] != "detached":
                        if attempts >= 10:
                            _logger.debug(
                                f"Ignoring NetworkInterface: {i['NetworkInterfaceId']} after 10 detach attempts."
                            )
                            break
                        _detach_network_interface(i["NetworkInterfaceId"], network_interface)
                        attempts += 1
                        time.sleep(3)
                    else:
                        network_interface.delete()
                        _logger.debug(f"NetWorkInterface {i['NetworkInterfaceId']} deleted.")
        except botocore.exceptions.ClientError as ex:
            error: Dict[str, Any] = ex.response["Error"]
            if "is currently in use" in error["Message"]:
                _logger.warning(f"Ignoring NetWorkInterface {i['NetworkInterfaceId']} because it stills in use.")
            elif "does not exist" in error["Message"]:
                _logger.warning(
                    f"Ignoring NetWorkInterface {i['NetworkInterfaceId']} because it does not exist anymore."
                )
            elif "You are not allowed to manage" in error["Message"]:
                _logger.warning(
                    f"Ignoring NetWorkInterface {i['NetworkInterfaceId']} because you are not allowed to manage."
                )
            elif "You do not have permission to access the specified resource" in error["Message"]:
                _logger.warning(
                    f"Ignoring NetWorkInterface {i['NetworkInterfaceId']} "
                    "because you do not have permission to access the specified resource."
                )
            else:
                raise


def delete_sec_group(manifest: Manifest, sec_group: str) -> None:
    ec2 = manifest.boto3_resource("ec2")
    try:
        sgroup = ec2.SecurityGroup(sec_group)
        if sgroup.ip_permissions:
            sgroup.revoke_ingress(IpPermissions=sgroup.ip_permissions)
        try:
            sgroup.delete()
        except botocore.exceptions.ClientError as ex:
            error: Dict[str, Any] = ex.response["Error"]
            if f"resource {sec_group} has a dependent object" not in error["Message"]:
                raise
            time.sleep(60)
            _logger.warning(f"Waiting 60 seconds to have {sec_group} free of dependents.")
            sgroup.delete()
    except botocore.exceptions.ClientError as ex:
        error = ex.response["Error"]
        if f"The security group '{sec_group}' does not exist" not in error["Message"]:
            _logger.warning(f"Ignoring security group {sec_group} because it does not exist anymore.")
        elif f"resource {sec_group} has a dependent object" not in error["Message"]:
            _logger.warning(f"Ignoring security group {sec_group} because it has a dependent object")
        else:
            raise


def _security_group(manifest: Manifest, vpc_id: str) -> None:
    client = manifest.boto3_client("ec2")
    sec_groups: List[str] = [
        s["GroupId"]
        for s in client.describe_security_groups()["SecurityGroups"]
        if s["VpcId"] == vpc_id and s["GroupName"] != "default"
    ]
    if sec_groups:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(sec_groups)) as executor:
            list(executor.map(delete_sec_group, repeat(manifest), sec_groups))


def _endpoints(manifest: Manifest, vpc_id: str) -> None:
    client = manifest.boto3_client("ec2")
    paginator = client.get_paginator("describe_vpc_endpoints")
    response_iterator = paginator.paginate(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}], MaxResults=25)
    for resp in response_iterator:
        endpoint_ids: List[str] = []
        for endpoint in resp["VpcEndpoints"]:
            endpoint_id: str = cast(str, endpoint["VpcEndpointId"])
            _logger.debug("VPC endpoint %s found", endpoint_id)
            endpoint_ids.append(endpoint_id)
        _logger.debug("Deleting endpoints: %s", endpoint_ids)
        if endpoint_ids:
            resp = client.delete_vpc_endpoints(VpcEndpointIds=endpoint_ids)
            _logger.debug("resp:\n%s", pprint.pformat(resp))


def demo_remaining_dependencies(manifest: Manifest, vpc_id: Optional[str] = None) -> None:
    if vpc_id is None:
        if manifest.vpc.vpc_id is None:
            manifest.fetch_ssm()
        if manifest.vpc.vpc_id is None:
            manifest.fetch_network_data()
        if manifest.vpc.vpc_id is None:
            _logger.debug(
                "Skipping _cleanup_remaining_dependencies() because manifest.vpc.vpc_id: %s", manifest.vpc.vpc_id
            )
            return None
        vpc_id = manifest.vpc.vpc_id
    elb.delete_load_balancers(manifest=manifest)
    _endpoints(manifest=manifest, vpc_id=vpc_id)
    _network_interface(manifest=manifest, vpc_id=vpc_id)
    _security_group(manifest=manifest, vpc_id=vpc_id)