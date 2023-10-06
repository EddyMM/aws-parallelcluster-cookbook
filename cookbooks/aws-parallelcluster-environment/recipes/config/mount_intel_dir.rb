# frozen_string_literal: true

#
# Copyright:: 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the
# License. A copy of the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "LICENSE.txt" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES
# OR CONDITIONS OF ANY KIND, express or implied. See the License for the specific language governing permissions and
# limitations under the License.

return if on_docker?

case node['cluster']['node_type']
when 'HeadNode'
  Chef::Log.info("Mount only on the ComputeFleet and LoginNodes")
when 'ComputeFleet', 'LoginNode'
  # Mount /opt/intel over NFS only if it exists
  exported_intel_dir = format_directory('/opt/intel')
  volume "mount /opt/intel" do
    action :mount
    shared_dir '/opt/intel'
    device(lazy { "#{node['cluster']['head_node_private_ip']}:#{exported_intel_dir}" })
    fstype 'nfs'
    options node['cluster']['nfs']['hard_mount_options']
    retries 10
    retry_delay 6
    only_if { ::File.directory?("/opt/intel") }
  end

else
  raise "node_type must be HeadNode or ComputeFleet"
end
