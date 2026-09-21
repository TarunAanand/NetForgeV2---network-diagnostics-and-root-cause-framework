from analysis.rules.link_rules import (
    AllInterfacesDownRule,
    ActivePacketDropsRule,
)
from analysis.rules.gateway_rules import (
    NoDefaultGatewayRule,
    GatewayUnreachableRule,
    GatewayHealthyWANOutageRule,
    TotalInternetOutageRule,
)
from analysis.rules.dns_rules import (
    DNSFailureWithHealthyIPRule,
    SlowDNSResolutionRule,
    DNSTimeoutRule,
)
from analysis.rules.transit_rules import (
    SeverePacketLossRule,
    HighJitterLatencySpikeRule,
)
from analysis.rules.transport_rules import (
    PortBlockedByFirewallRule,
    TargetSpecificFailureRule,
)
from analysis.rules.host_rules import (
    HostSaturationBufferbloatRule,
)
from analysis.rules.path_rules import (
    PathChangeRule,
    HighHopLossRule,
    LastMileVsCoreRule,
)
from analysis.rules.link_domain_rules import (
    LinkSaturationRule,
    InterfaceErrorsRule,
)
from analysis.rules.flow_rules import (
    ElephantFlowRule,
)
from analysis.rules.cross_rules import (
    LinkCongestionWithPathLossRule,
)
from analysis.rules.baseline_rules import (
    SuddenDegradationRule,
)
from analysis.rules.mesh_rules import (
    MeshPartitionRule,
)

DEFAULT_RULES = [
    AllInterfacesDownRule(),
    ActivePacketDropsRule(),
    NoDefaultGatewayRule(),
    GatewayUnreachableRule(),
    GatewayHealthyWANOutageRule(),
    TotalInternetOutageRule(),
    DNSFailureWithHealthyIPRule(),
    DNSTimeoutRule(),
    SlowDNSResolutionRule(),
    SeverePacketLossRule(),
    HighJitterLatencySpikeRule(),
    PortBlockedByFirewallRule(),
    TargetSpecificFailureRule(),
    HostSaturationBufferbloatRule(),
    PathChangeRule(),
    HighHopLossRule(),
    LastMileVsCoreRule(),
    LinkSaturationRule(),
    InterfaceErrorsRule(),
    ElephantFlowRule(),
    LinkCongestionWithPathLossRule(),
    SuddenDegradationRule(),
    MeshPartitionRule(),
]
