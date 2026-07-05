# Copyright 2018 GoDaddy
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import ipaddress
import testtools

from oslo_log import log as logging
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib import decorators
from tempest.lib import exceptions

from octavia_tempest_plugin.common import constants as const
from octavia_tempest_plugin.tests import test_base
from octavia_tempest_plugin.tests import utils
from octavia_tempest_plugin.tests import waiters

CONF = config.CONF
LOG = logging.getLogger(__name__)


class _TrafficAdditionalVIPScenarioTest(
        test_base.LoadBalancerBaseTestWithCompute):

    vip_subnet = None
    additional_vip_subnets = []

    protocol_port = utils.PortNumberIterator()

    @classmethod
    def skip_checks(cls):
        super().skip_checks()

        if not CONF.validation.run_validation:
            raise cls.skipException('Traffic tests will not work without '
                                    'run_validation enabled.')

        if CONF.load_balancer.test_with_noop:
            raise cls.skipException('Traffic tests will not work in noop '
                                    'mode.')

        if not CONF.load_balancer.test_with_ipv6:
            raise cls.skipException('Mixed IPv4/IPv6 traffic test requires '
                                    'IPv6')

    @classmethod
    def resource_setup(cls):
        """Setup resources needed by the tests."""

        super().resource_setup(extra_subnets=True)

        if not cls.mem_lb_client.is_version_supported(
                cls.api_version, '2.26'):
            raise cls.skipException(
                'Additional VIPs feature require '
                'Octavia API version 2.26 or newer.')

        vip_data = {
            const.VIP_SUBNET_ID: getattr(cls, cls.vip_subnet)[const.ID],
            const.ADDITIONAL_VIPS: [
                {const.SUBNET_ID: getattr(cls, add_vip_subnet)[const.ID]}
                for add_vip_subnet in cls.additional_vip_subnets
            ]
        }

        lb_name = data_utils.rand_name("lb_member_lb1_operations")
        lb_kwargs = {const.PROVIDER: CONF.load_balancer.provider,
                     const.NAME: lb_name}
        lb_kwargs.update(vip_data)

        lb = cls.mem_lb_client.create_loadbalancer(**lb_kwargs)
        cls.lb = lb
        cls.lb_id = lb[const.ID]
        cls.addClassResourceCleanup(
            cls.mem_lb_client.cleanup_loadbalancer,
            cls.lb_id, cascade=True)

        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.lb_build_interval,
                                CONF.load_balancer.lb_build_timeout)

        cls.lb_vips = cls._get_vips(lb)

    @classmethod
    def _listener_pool_create(cls, protocol, protocol_port, lb_algorithm):
        listener_name = data_utils.rand_name("lb_member_listener1_operations")
        listener_kwargs = {
            const.NAME: listener_name,
            const.PROTOCOL: protocol,
            const.PROTOCOL_PORT: protocol_port,
            const.LOADBALANCER_ID: cls.lb_id,
        }
        listener = cls.mem_listener_client.create_listener(**listener_kwargs)

        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

        pool_name = data_utils.rand_name("lb_member_pool1_operations")
        pool_kwargs = {
            const.NAME: pool_name,
            const.PROTOCOL: protocol,
            const.LB_ALGORITHM: lb_algorithm,
            const.LISTENER_ID: listener[const.ID],
        }
        # This is a special case as the reference driver does not support
        # SOURCE-IP-PORT. Since it runs with not_implemented_is_error, we must
        # handle this test case special.
        try:
            pool = cls.mem_pool_client.create_pool(**pool_kwargs)
        except exceptions.NotImplemented as e:
            if lb_algorithm != const.LB_ALGORITHM_SOURCE_IP_PORT:
                raise
            message = ("The configured provider driver '{driver}' "
                       "does not support a feature required for this "
                       "test.".format(driver=CONF.load_balancer.provider))
            if hasattr(e, 'resp_body'):
                message = e.resp_body.get('faultstring', message)
            raise testtools.TestCase.skipException(message)

        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

        # Set up Member 1 for IPv4 Webserver 1
        member1_name = data_utils.rand_name("lb_member_member1-traffic")
        member1_kwargs = {
            const.POOL_ID: pool[const.ID],
            const.NAME: member1_name,
            const.ADMIN_STATE_UP: True,
            const.ADDRESS: cls.webserver1_ip,
            const.PROTOCOL_PORT: 80,
        }
        if cls.lb_member_1_subnet:
            member1_kwargs[const.SUBNET_ID] = cls.lb_member_1_subnet[const.ID]

        cls.mem_member_client.create_member(**member1_kwargs)
        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

        # Set up Member 2 for IPv6 Webserver 1
        member2_name = data_utils.rand_name("lb_member_member2-traffic")
        member2_kwargs = {
            const.POOL_ID: pool[const.ID],
            const.NAME: member2_name,
            const.ADMIN_STATE_UP: True,
            const.ADDRESS: cls.webserver1_ipv6,
            const.PROTOCOL_PORT: 80,
        }
        if cls.lb_member_1_ipv6_subnet:
            member2_kwargs[const.SUBNET_ID] = (
                cls.lb_member_1_ipv6_subnet[const.ID])

        cls.mem_member_client.create_member(**member2_kwargs)
        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

        # Set up Member 3 for IPv4 Webserver 2
        member3_name = data_utils.rand_name("lb_member_member3-traffic")
        member3_kwargs = {
            const.POOL_ID: pool[const.ID],
            const.NAME: member3_name,
            const.ADMIN_STATE_UP: True,
            const.ADDRESS: cls.webserver2_ip,
            const.PROTOCOL_PORT: 80,
        }
        if cls.lb_member_2_subnet:
            member3_kwargs[const.SUBNET_ID] = cls.lb_member_2_subnet[const.ID]

        cls.mem_member_client.create_member(**member3_kwargs)
        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

        # Set up Member 4 for IPv6 Webserver 2
        member4_name = data_utils.rand_name("lb_member_member4-traffic")
        member4_kwargs = {
            const.POOL_ID: pool[const.ID],
            const.NAME: member4_name,
            const.ADMIN_STATE_UP: True,
            const.ADDRESS: cls.webserver2_ipv6,
            const.PROTOCOL_PORT: 80,
        }
        if cls.lb_member_2_ipv6_subnet:
            member4_kwargs[const.SUBNET_ID] = (
                cls.lb_member_2_ipv6_subnet[const.ID])

        cls.mem_member_client.create_member(**member4_kwargs)
        waiters.wait_for_status(cls.mem_lb_client.show_loadbalancer,
                                cls.lb_id, const.PROVISIONING_STATUS,
                                const.ACTIVE,
                                CONF.load_balancer.build_interval,
                                CONF.load_balancer.build_timeout)

    @classmethod
    def _get_vips(cls, lb):
        lb_port_vips = [(lb[const.VIP_PORT_ID], lb[const.VIP_ADDRESS])]
        for vip in lb[const.ADDITIONAL_VIPS]:
            lb_port_vips.append((vip.get(const.PORT_ID),
                                 vip.get(const.IP_ADDRESS)))
        LOG.debug("LB %s has VIPs: %s", lb[const.ID], lb_port_vips)

        # Make floating ips if necessary for ipv4 VIPs
        public_vips = []
        for vip_port_id, vip_addr in lb_port_vips:
            vip_obj = ipaddress.ip_address(vip_addr)
            if (CONF.validation.connect_method == 'floating' and
                    vip_obj.version == 4):
                # Build kwargs for floating IP creation
                fip_kwargs = {
                    'floating_network_id': CONF.network.public_network_id,
                    'port_id': vip_port_id
                }
                # Only add fixed_ip_address if port_id is provided
                # (required when port has multiple IPv4 addresses)
                if vip_port_id:
                    fip_kwargs['fixed_ip_address'] = vip_addr

                result = cls.lb_mem_float_ip_client.create_floatingip(
                    **fip_kwargs)

                floating_ip = result['floatingip']
                floating_address = floating_ip['floating_ip_address']
                LOG.info('Created Floating IP for VIP: %s->%s',
                         floating_address, vip_addr)
                cls.addClassResourceCleanup(
                    waiters.wait_for_not_found,
                    cls.lb_mem_float_ip_client.delete_floatingip,
                    cls.lb_mem_float_ip_client.show_floatingip,
                    floatingip_id=floating_ip['id'])
                # Just mask the original VIP with the floating one for return
                public_vips.append(floating_address)
            else:
                public_vips.append(vip_addr)
        return public_vips

    def _check_vips(self, protocol, protocol_port, lb_algorithm, delay=None):
        members = 2
        if lb_algorithm == const.LB_ALGORITHM_SOURCE_IP:
            members = 1

        # Separate VIPs into IPv4 and IPv6
        ipv4_vips = []
        ipv6_vips = []
        for vip in self.lb_vips:
            ip_obj = ipaddress.ip_address(vip)
            if ip_obj.version == 6:
                ipv6_vips.append(vip)
            else:
                ipv4_vips.append(vip)

        # Check IPv4 VIPs from test node
        for vip in ipv4_vips:
            LOG.debug("Check traffic on IPv4 VIP %s", vip)
            self.check_members_balanced(vip, protocol=protocol,
                                        protocol_port=protocol_port,
                                        persistent=False,
                                        traffic_member_count=members,
                                        delay=delay,
                                        lb_algorithm=lb_algorithm)

        # Check IPv6 VIPs from VM (required due to routing)
        if ipv6_vips:
            from tempest.lib.common.utils.linux import remote_client

            ssh_client = remote_client.RemoteClient(
                self.webserver1_public_ip,
                CONF.validation.image_ssh_user,
                pkey=self.lb_member_keypair['private_key'],
                **self.remote_client_args())

            for vip in ipv6_vips:
                LOG.debug("Check traffic on IPv6 VIP %s from VM", vip)
                self.check_members_balanced_from_vm(
                    ssh_client, vip,
                    protocol=protocol,
                    protocol_port=protocol_port,
                    traffic_member_count=members,
                    delay=delay,
                    lb_algorithm=lb_algorithm)

    def _check_vips_from_vm(self, protocol, protocol_port, lb_algorithm,
                            delay=None):
        """Check VIPs from within a VM instead of from the test node.

        This is useful for IPv6 VIPs that may not be routable from the
        test node but are accessible from VMs in the network.
        """
        members = 2
        if lb_algorithm == const.LB_ALGORITHM_SOURCE_IP:
            members = 1

        # Use webserver1's SSH connection to test the VIPs
        from tempest.lib.common.utils.linux import remote_client

        ssh_client = remote_client.RemoteClient(
            self.webserver1_public_ip,
            CONF.validation.image_ssh_user,
            pkey=self.lb_member_keypair['private_key'],
            **self.remote_client_args())

        for vip in self.lb_vips:
            LOG.debug("Check traffic on VIP %s from VM", vip)
            self.check_members_balanced_from_vm(
                ssh_client, vip,
                protocol=protocol,
                protocol_port=protocol_port,
                traffic_member_count=members,
                delay=delay,
                lb_algorithm=lb_algorithm)

    def _test_additional_vips(self, protocol, lb_algorithm, delay=None):
        """Tests sending traffic through all of a loadbalancer's multiple VIPs

        * Set up a LB with multiple VIPs
        * Test traffic to ensure it is balanced properly.
        """
        protocol_port = next(self.protocol_port)

        self._listener_pool_create(protocol, protocol_port, lb_algorithm)
        self._check_vips_from_vm(
            protocol, protocol_port, lb_algorithm, delay=delay)


class TrafficIPv4AdditionalIPv6VIPScenarioTest(
        _TrafficAdditionalVIPScenarioTest):

    vip_subnet = 'lb_member_vip_subnet'
    additional_vip_subnets = ['lb_member_vip_subnet2',
                              'lb_member_vip_ipv6_subnet2']

    @decorators.idempotent_id('a8861ed9-d053-4131-b27e-2d8d9038d060')
    def test_ipv4_additional_vips_round_robin_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_ROUND_ROBIN)

    @decorators.idempotent_id('c8a2fa1e-1a94-404b-b1c6-a3e504a14cde')
    def test_ipv4_additional_vips_round_robin_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_ROUND_ROBIN,
                                   delay=0.2)

    @decorators.idempotent_id('47ecdd1d-268c-4e5b-96a2-f91713220fa7')
    def test_ipv4_additional_vips_round_robin_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_ROUND_ROBIN,
                                   delay=0.2)

    @decorators.idempotent_id('417a421c-659a-4218-9754-35fdbd19191e')
    def test_ipv4_additional_vips_least_connections_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS)

    @decorators.idempotent_id('5207db80-fb9c-4b43-9ac0-add15b48a6e8')
    def test_ipv4_additional_vips_least_connections_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS,
                                   delay=0.2)

    @decorators.idempotent_id('0979c4aa-10a8-4ea4-bca7-2bce774e4847')
    def test_ipv4_additional_vips_least_connections_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS,
                                   delay=0.2)

    @decorators.idempotent_id('2a500c5a-404d-44e1-9ed1-379813eec8ea')
    def test_ipv4_additional_vips_source_ip_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_SOURCE_IP)

    @decorators.idempotent_id('45ea9e09-fe80-4b95-a20d-da0db6ca1d2a')
    def test_ipv4_additional_vips_source_ip_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_SOURCE_IP,
                                   delay=0.2)

    @decorators.idempotent_id('09b1e9f4-e6af-4f55-9f5f-12a0bd215031')
    def test_ipv4_additional_vips_source_ip_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_SOURCE_IP,
                                   delay=0.2)

    @decorators.idempotent_id('19443cf8-e4dc-4b55-a970-0a2e0860e239')
    def test_ipv4_additional_vips_source_ip_port_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT)

    @decorators.idempotent_id('8341a59b-613c-4bbb-acce-7e9077cf85ea')
    def test_ipv4_additional_vips_source_ip_port_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT,
                                   delay=0.2)

    @decorators.idempotent_id('ba9c2a64-23fa-421b-aec5-64b02b213f10')
    def test_ipv4_additional_vips_source_ip_port_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT,
                                   delay=0.2)


class TrafficIPv6AdditionalIPv4VIPScenarioTest(
        _TrafficAdditionalVIPScenarioTest):

    vip_subnet = 'lb_member_vip_ipv6_subnet'
    additional_vip_subnets = ['lb_member_vip_subnet2',
                              'lb_member_vip_ipv6_subnet2']

    @decorators.idempotent_id('80a2581d-5c24-4043-8501-41547b98f64b')
    def test_ipv6_additional_vips_round_robin_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_ROUND_ROBIN)

    @decorators.idempotent_id('7933e421-8581-4db3-9d61-4d02e90de4ae')
    def test_ipv6_additional_vips_round_robin_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_ROUND_ROBIN,
                                   delay=0.2)

    @decorators.idempotent_id('76156a40-5974-471b-a02d-db35cad08d9a')
    def test_ipv6_additional_vips_round_robin_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_ROUND_ROBIN,
                                   delay=0.2)

    @decorators.idempotent_id('c7680a60-1708-4f41-b1e3-d0b29c11a450')
    def test_ipv6_additional_vips_least_connections_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS)

    @decorators.idempotent_id('5a57f0c2-27ae-49a8-86bf-38ad31bbb4f7')
    def test_ipv6_additional_vips_least_connections_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS,
                                   delay=0.2)

    @decorators.idempotent_id('473705cd-7340-4c18-905c-95b2334afb02')
    def test_ipv6_additional_vips_least_connections_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_LEAST_CONNECTIONS,
                                   delay=0.2)

    @decorators.idempotent_id('638a98b1-d763-4c8a-8547-7a086d5b1984')
    def test_ipv6_additional_vips_source_ip_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_SOURCE_IP)

    @decorators.idempotent_id('66bcad6c-bfe2-4334-bfef-dd8b317d78ea')
    def test_ipv6_additional_vips_source_ip_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_SOURCE_IP,
                                   delay=0.2)

    @decorators.idempotent_id('7ce8973f-c0eb-45eb-a3f4-b54847eb4f50')
    def test_ipv6_additional_vips_source_ip_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_SOURCE_IP,
                                   delay=0.2)

    @decorators.idempotent_id('47c2cb7b-199d-4a43-8c47-672735846829')
    def test_ipv6_additional_vips_source_ip_port_http_traffic(self):
        self._test_additional_vips(const.HTTP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT)

    @decorators.idempotent_id('338c3767-9235-486f-84f5-966f54e7de97')
    def test_ipv6_additional_vips_source_ip_port_tcp_traffic(self):
        self._test_additional_vips(const.TCP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT,
                                   delay=0.2)

    @decorators.idempotent_id('4087d97a-ba86-4770-8bb4-d3dcca2b59e3')
    def test_ipv6_additional_vips_source_ip_port_udp_traffic(self):
        self._test_additional_vips(const.UDP,
                                   const.LB_ALGORITHM_SOURCE_IP_PORT,
                                   delay=0.2)
