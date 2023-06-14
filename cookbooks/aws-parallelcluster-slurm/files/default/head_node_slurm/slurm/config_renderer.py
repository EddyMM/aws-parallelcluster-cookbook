import logging
from typing import Dict, List

from config_utils import (
    get_efa_settings,
    get_instance_types,
    get_min_gpu_count_and_type,
    get_min_vcpus,
    get_real_memory,
)

CONFIG_HEADER = "# This file is automatically generated by pcluster"
log = logging.getLogger()


class ComputeResourceRenderer:
    """Renders a PCluster ComputeResource Config as a Slurm NodeName config, gres config or node_set element."""

    def __init__(self, queue_name: str, compute_resource_config: Dict, no_gpu, memory_ratio, instance_types_info: Dict):
        self.queue_name = queue_name
        self.has_gpu = not no_gpu
        self.static_nodes = compute_resource_config["MinCount"]
        self.dynamic_nodes = compute_resource_config["MaxCount"] - self.static_nodes
        self.name = compute_resource_config["Name"]
        self.disable_multithreading = compute_resource_config["DisableSimultaneousMultithreading"]
        self.custom_settings = compute_resource_config.get("CustomSlurmSettings", {})
        self.spot_price = compute_resource_config.get("SpotPrice", None)
        self.instance_types = get_instance_types(compute_resource_config)
        self.real_memory = get_real_memory(
            compute_resource_config, self.instance_types, instance_types_info, memory_ratio
        )
        self.efa_enabled, self.efa_gdr_support = get_efa_settings(compute_resource_config)
        self.instance_types = get_instance_types(compute_resource_config)
        self.vcpus_count, self.threads_per_core = get_min_vcpus(self.instance_types, instance_types_info)
        self.gpu_count, self.gpu_type = get_min_gpu_count_and_type(self.instance_types, instance_types_info, log)
        self.static_node_priority = compute_resource_config["StaticNodePriority"]
        self.dynamic_node_priority = compute_resource_config["DynamicNodePriority"]

    def render_as_nodename(self):
        """Launch the rendering process."""
        config = ""
        if self.static_nodes > 0:
            config += f"NodeName={self._static_node_name()}{self._definitions()}{self._custom_settings()}\n"
        if self.dynamic_nodes > 0:
            config += (
                f"NodeName={self._dynamic_node_name()}{self._definitions(dynamic=True)}{self._custom_settings()}\n"
            )

        return config

    def render_as_nodeset_element(self) -> List[str]:
        """Alternative rendering for the NodeSet definition."""
        nodeset = []
        if self.static_nodes > 0:
            nodeset.append(f"{self._static_node_name()}")
        if self.dynamic_nodes > 0:
            nodeset.append(f"{self._dynamic_node_name()}")

        return nodeset

    def render_as_gres_element(self):
        """Alternative rendering for the gres config."""
        gres_render = ""
        if self.gpu_count > 0:
            if self.static_nodes > 0:
                gres_render += (
                    f"NodeName={self._static_node_name()} Name=gpu "
                    f"Type={self.gpu_type} File=/dev/nvidia[0-{self.gpu_count - 1}]\n"
                )
            if self.dynamic_nodes > 0:
                gres_render += (
                    f"NodeName={self._dynamic_node_name()} Name=gpu "
                    f"Type={self.gpu_type} File=/dev/nvidia[0-{self.gpu_count - 1}]\n"
                )

        return gres_render

    def _definitions(self, dynamic=False):
        definitions = f" CPUs={self._vcpus()} RealMemory={self.real_memory} State=CLOUD {self._features(dynamic)}"
        definitions += f" Weight={self.dynamic_node_priority if dynamic else self.static_node_priority}"

        if self.has_gpu and self.gpu_count > 0:
            definitions += f" Gres=gpu:{ self.gpu_type }:{self.gpu_count}"

        return definitions

    def _features(self, dynamic=False):
        resource_type = "static"
        if dynamic:
            resource_type = "dynamic"

        instance_type = f",{self.instance_types[0]}"
        if len(self.instance_types) > 1:
            # When multiple instance types are defined we do not know in advance which one will be used
            # to launch the node. So we do not list any of them as feature
            instance_type = ""

        features = f"Feature={resource_type}{instance_type},{self.name}"
        if self.efa_enabled:
            features += ",efa"

        if self.gpu_count > 0:
            features += ",gpu"

        return features

    def _custom_settings(self):
        custom = ""
        for param, value in self.custom_settings.items():
            custom += f" {param}={value}"

        return custom

    def _static_node_name(self):
        """Render the NodeName section for static nodes."""
        return self._node_name("st", self.static_nodes)

    def _dynamic_node_name(self):
        """Render the NodeName section for dynamic nodes."""
        return self._node_name("dy", self.dynamic_nodes)

    def _node_name(self, type, size):
        return f"{self.queue_name}-{type}-{self.name}-[1-{size}]"

    def _vcpus(self) -> int:
        """Return the number of vcpus according to disable_hyperthreading and instance features."""
        return self.vcpus_count if not self.disable_multithreading else (self.vcpus_count // self.threads_per_core)

    def _gpus(self) -> dict:
        """Return the number of GPUs and type for the compute resource."""
        return {"count": self.gpu_count, "type": self.gpu_type}


class QueueRenderer:
    """Renders a PCluster Queue Config as a Slurm partition config or as gres config."""

    def __init__(self, queue_config, no_gpu, memory_ratio, instance_types_info, conf_type="partition", default=False):
        self.name = queue_config["Name"]
        self.is_default = default
        self.conf_type = conf_type
        self.custom_settings = queue_config.get("CustomSlurmSettings", {})
        self.compute_renderers = [
            ComputeResourceRenderer(self.name, compute_resource_config, no_gpu, memory_ratio, instance_types_info)
            for compute_resource_config in queue_config["ComputeResources"]
        ]
        self.job_exclusive_instance_allocation = queue_config.get("JobExclusiveInstanceAllocation")

    def render_config(self):
        """Launch the rendering of the required configuration."""
        config = f"{CONFIG_HEADER}\n"
        if self.conf_type == "gres":
            config += self._render_as_gres_config()
        else:
            config += self._render_as_partition_config()

        return config

    def _render_as_partition_config(self):
        partition_config = "\n"
        for renderer in self.compute_renderers:
            partition_config += f"{renderer.render_as_nodename()}"

        partition_config += f"\n{self._render_nodeset()}\n{self._render_partition()}\n"
        return partition_config

    def _render_as_gres_config(self):
        gres = ""
        for renderer in self.compute_renderers:
            gres += f"{renderer.render_as_gres_element()}"

        return gres

    def _render_nodeset(self):
        nodeset = f"NodeSet={self.name}_nodes Nodes="
        nodes = []
        for renderer in self.compute_renderers:
            nodes.extend(renderer.render_as_nodeset_element())

        nodeset += ",".join(nodes)

        return nodeset

    def _render_partition(self):
        partition = f"PartitionName={self.name} Nodes={self.name}_nodes MaxTime=INFINITE State=UP"
        if self.is_default:
            partition += " Default=YES"

        if self.job_exclusive_instance_allocation:
            partition += " OverSubscribe=NO"

        partition += f"{self._custom_settings()}"
        return partition

    def _custom_settings(self):
        custom = ""
        for param, value in self.custom_settings.items():
            custom += f" {param}={value}"

        return custom
