# Copyright 2017 GoDaddy
# Copyright 2017 Catalyst IT Ltd
# Copyright 2018 Rackspace US Inc.  All rights reserved.
# Copyright 2020 Red Hat, Inc. All rights reserved.
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
import errno
import ipaddress
import requests
import socket
import time
from urllib.parse import urlparse

from oslo_log import log as logging
from tempest import config
from tempest.lib import exceptions
from tempest import test

from octavia_tempest_plugin.common import constants as const
from octavia_tempest_plugin.common import requests_adapters

CONF = config.CONF
LOG = logging.getLogger(__name__)


class ValidatorsMixin(test.BaseTestCase):

    @staticmethod
    def validate_URL_response(
            URL, expected_status_code=200, requests_session=None,
            expected_body=None, HTTPS_verify=True, client_cert_path=None,
            CA_certs_path=None, source_port=None,
            request_interval=CONF.load_balancer.build_interval,
            request_timeout=CONF.load_balancer.build_timeout):
        """Check a URL response (HTTP or HTTPS).

        :param URL: The URL to query.
        :param expected_status_code: The expected HTTP status code.
        :param requests_session: A requests session to use for the request.
                                 If None, a new session will be created.
        :param expected_body: The expected response text, None will not
                              compare.
        :param HTTPS_verify: Should we verify the HTTPS server.
        :param client_cert_path: Filesystem path to a file with the client
                                 private key and certificate.
        :param CA_certs_path: Filesystem path to a file containing CA
                              certificates to use for HTTPS validation.
        :param source_port: If set, the request will come from this source port
                            number. If None, a random port will be used.
        :param request_interval: Time, in seconds, to timeout a request.
        :param request_timeout: The maximum time, in seconds, to attempt
                                requests.  Failed validation of expected
                                results does not result in a retry.
        :raises InvalidHttpSuccessCode: The expected_status_code did not match.
        :raises InvalidHTTPResponseBody: The response body did not match the
                                         expected content.
        :raises TimeoutException: The request timed out.
        :returns: The response data.
        """
        session = requests_session
        if requests_session is None:
            session = requests.Session()
        if source_port:
            session.mount('http://',
                          requests_adapters.SourcePortAdapter(source_port))
            session.mount('https://',
                          requests_adapters.SourcePortAdapter(source_port))

        session_kwargs = {}
        if not HTTPS_verify:
            session_kwargs['verify'] = False
        if CA_certs_path:
            session_kwargs['verify'] = CA_certs_path
        if client_cert_path:
            session_kwargs['cert'] = client_cert_path
        session_kwargs['timeout'] = request_interval
        start = time.time()
        while time.time() - start < request_timeout:
            try:
                response = session.get(URL, **session_kwargs)
                response_status_code = response.status_code
                response_text = response.text
                response.close()
                if response_status_code != expected_status_code:
                    raise exceptions.InvalidHttpSuccessCode(
                        '{0} is not the expected code {1}'.format(
                            response_status_code, expected_status_code))
                if expected_body and response_text != expected_body:
                    details = '{} does not match expected {}'.format(
                        response_text, expected_body)
                    raise exceptions.InvalidHTTPResponseBody(
                        resp_body=details)
                if requests_session is None:
                    session.close()
                return response_text
            except requests.exceptions.Timeout:
                # Don't sleep as we have already waited the interval.
                LOG.info('Request for %s timed out. Retrying.', URL)
            except (exceptions.InvalidHttpSuccessCode,
                    exceptions.InvalidHTTPResponseBody,
                    requests.exceptions.SSLError):
                if requests_session is None:
                    session.close()
                raise
            except Exception as e:
                if "[Errno 98] Address already in use" in str(e):
                    raise e
                LOG.info('Validate URL got exception: %s. '
                         'Retrying.', e)
                time.sleep(request_interval)
        if requests_session is None:
            session.close()
        raise exceptions.TimeoutException()

    @classmethod
    def make_udp_request(cls, vip_address, port=80, timeout=None,
                         source_port=None):
        if ipaddress.ip_address(vip_address).version == 6:
            family = socket.AF_INET6
        else:
            family = socket.AF_INET

        sock = socket.socket(family, socket.SOCK_DGRAM)

        # Force the use of an incremental port number for source to avoid
        # re-use of a previous source port that will affect the round-robin
        # dispatch
        while True:
            port_number = cls.src_port_number
            cls.src_port_number += 1
            if cls.src_port_number >= cls.SRC_PORT_NUMBER_MAX:
                cls.src_port_number = cls.SRC_PORT_NUMBER_MIN

            # catch and skip already used ports on the host
            try:
                if source_port:
                    sock.bind(('', source_port))
                else:
                    sock.bind(('', port_number))
            except OSError as e:
                # if error is 'Address already in use', try next port number
                # If source_port is defined and already in use, a test
                # developer has made a mistake by using a duplicate source
                # port.
                if e.errno != errno.EADDRINUSE or source_port:
                    raise e
            else:
                # successfully bind the socket
                break

        server_address = (vip_address, port)
        data = b"data\n"

        if timeout is not None:
            sock.settimeout(timeout)

        try:
            sock.sendto(data, server_address)
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            # Normalize the timeout exception so that UDP and other protocol
            # tests all return a common timeout exception.
            raise exceptions.TimeoutException()
        finally:
            sock.close()

        return data.decode('utf-8')

    def make_request(
            self, vip_address, protocol=const.HTTP, HTTPS_verify=True,
            protocol_port=80, requests_session=None, client_cert_path=None,
            CA_certs_path=None, request_timeout=2, source_port=None):
        """Make a request to a VIP.

        :param vip_address: The VIP address to test.
        :param protocol: The protocol to use for the test.
        :param HTTPS_verify: How to verify the TLS certificate. True: verify
                             using the system CA certificates. False: Do not
                             verify the VIP certificate. <path>: Filesytem path
                             to a CA certificate bundle file or directory. For
                             directories, the directory must be processed using
                             the c_rehash utility from openssl.
        :param protocol_port: The port number to use for the test.
        :param requests_session: A requests session to use for the request.
                                 If None, a new session will be created.
        :param request_timeout: The maximum time, in seconds, to attempt
                                requests.
        :param client_cert_path: Filesystem path to a file with the client
                                 private key and certificate.
        :param CA_certs_path: Filesystem path to a file containing CA
                              certificates to use for HTTPS validation.
        :param source_port: If set, the request will come from this source port
                            number. If None, a random port will be used.
        :raises InvalidHttpSuccessCode: The expected_status_code did not match.
        :raises InvalidHTTPResponseBody: The response body did not match the
                                         expected content.
        :raises TimeoutException: The request timed out.
        :raises Exception: If a protocol is requested that is not implemented.
        :returns: The response data.
        """
        # Note: We are using HTTP as the TCP protocol check to simplify
        #       the test setup. HTTP is a TCP based protocol.
        if protocol == const.HTTP or protocol == const.TCP:
            url = "http://{0}{1}{2}".format(
                vip_address, ':' if protocol_port else '',
                protocol_port or '')
            data = self.validate_URL_response(
                url, HTTPS_verify=False, requests_session=requests_session,
                request_timeout=request_timeout,
                source_port=source_port)
        elif (protocol == const.HTTPS or
              protocol == const.TERMINATED_HTTPS):
            url = "https://{0}{1}{2}".format(
                vip_address, ':' if protocol_port else '',
                protocol_port or '')
            data = self.validate_URL_response(
                url, HTTPS_verify=HTTPS_verify,
                requests_session=requests_session,
                client_cert_path=client_cert_path,
                CA_certs_path=CA_certs_path, source_port=source_port,
                request_timeout=request_timeout)
        elif protocol == const.UDP:
            data = self.make_udp_request(
                vip_address, port=protocol_port, timeout=request_timeout,
                source_port=source_port)
        else:
            message = ("Unknown protocol %s. Unable to check if the "
                       "load balancer is balanced.", protocol)
            LOG.error(message)
            raise Exception(message)
        return data

    def check_members_balanced(
            self, vip_address, traffic_member_count=2, protocol=const.HTTP,
            HTTPS_verify=True, protocol_port=80, persistent=True, repeat=20,
            client_cert_path=None, CA_certs_path=None, request_interval=2,
            request_timeout=10, source_port=None, delay=None,
            lb_algorithm=const.LB_ALGORITHM_ROUND_ROBIN):
        """Checks that members are evenly balanced behind a VIP.

        :param vip_address: The VIP address to test.
        :param traffic_member_count: The expected number of members.
        :param protocol: The protocol to use for the test.
        :param HTTPS_verify: How to verify the TLS certificate. True: verify
                             using the system CA certificates. False: Do not
                             verify the VIP certificate. <path>: Filesytem path
                             to a CA certificate bundle file or directory. For
                             directories, the directory must be processed using
                             the c_rehash utility from openssl.
        :param protocol_port: The port number to use for the test.
        :param persistent: True when the test should persist cookies and use
                           the protocol keepalive mechanism with the target.
                           This may include maintaining a connection to the
                           member server across requests.
        :param repeat: The number of requests to make against the VIP.
        :param request_timeout: The maximum time, in seconds, to attempt
                                requests.
        :param client_cert_path: Filesystem path to a file with the client
                                 private key and certificate.
        :param CA_certs_path: Filesystem path to a file containing CA
                              certificates to use for HTTPS validation.
        :param source_port: If set, the request will come from this source port
                            number. If None, a random port will be used.
        :param delay: The time to pause between requests in seconds, can be
                      fractional.
        :param lb_algorithm: Algorithm used in the pool to balance the traffic.
        """
        if (ipaddress.ip_address(vip_address).version == 6 and
                protocol != const.UDP):
            vip_address = '[{}]'.format(vip_address)

        requests_session = None
        if persistent:
            requests_session = requests.Session()

        self._wait_for_lb_functional(
            vip_address, traffic_member_count, protocol_port, protocol,
            HTTPS_verify, requests_session=requests_session,
            source_port=source_port)

        if source_port:
            LOG.debug('Using source port %s for request(s)', source_port)

        response_counts = {}
        # Send a number requests to lb vip
        for i in range(repeat):
            try:
                data = self.make_request(
                    vip_address, protocol=protocol, HTTPS_verify=HTTPS_verify,
                    protocol_port=protocol_port,
                    requests_session=requests_session,
                    client_cert_path=client_cert_path,
                    CA_certs_path=CA_certs_path, source_port=source_port,
                    request_timeout=request_timeout)

                if data in response_counts:
                    response_counts[data] += 1
                else:
                    response_counts[data] = 1
                if delay is not None:
                    time.sleep(delay)
            except Exception:
                LOG.exception('Failed to send request to loadbalancer vip')
                if persistent:
                    requests_session.close()
                raise Exception('Failed to connect to lb')
        if persistent:
            requests_session.close()
        LOG.debug('Loadbalancer response totals: %s', response_counts)

        # Ensure the correct number of members responded
        self.assertEqual(traffic_member_count, len(response_counts))

        # Just in case round-robin algorithm the number of request should
        # be evenly distributed (as long as the number of members is multiple
        # of the total number of requests made) or maximum distance of 1
        # between them
        if lb_algorithm == const.LB_ALGORITHM_ROUND_ROBIN:
            if repeat % traffic_member_count == 0:
                self.assertEqual(1, len(set(response_counts.values())))
            else:
                response_values = list(response_counts.values())
                response_values_distance = [
                    (s == t) or (s == t + 1)
                    for s, t in zip(response_values, response_values[1:])
                ]
                self.assertTrue(all(response_values_distance))

    def check_members_balanced_from_vm(
            self, ssh_client, vip_address, traffic_member_count=2,
            protocol=const.HTTP, protocol_port=80, repeat=20, delay=None,
            lb_algorithm=const.LB_ALGORITHM_ROUND_ROBIN, request_timeout=10):
        """Checks that members are evenly balanced behind a VIP from a VM.

        This method executes requests from within a VM using SSH,
        which is useful for testing VIPs that may not be reachable from
        the test node (e.g., IPv6 VIPs without proper routing) but are
        reachable from VMs within the OpenStack network.

        :param ssh_client: An SSH client connection to the VM (RemoteClient)
        :param vip_address: The VIP address to test
        :param traffic_member_count: The expected number of members
        :param protocol: The protocol to use (HTTP, HTTPS, TCP, UDP)
        :param protocol_port: The port number to use
        :param repeat: The number of requests to make against the VIP
        :param delay: The time to pause between requests in seconds
        :param lb_algorithm: Algorithm used in the pool
        :param request_timeout: Timeout for each request in seconds
        """
        ip_obj = ipaddress.ip_address(vip_address)

        LOG.debug('Testing VIP %s:%s (%s) from VM using SSH',
                  vip_address, protocol_port, protocol)

        response_counts = {}
        failed_requests = 0

        # Determine the command based on protocol
        if protocol == const.UDP:
            # For UDP, use nc (netcat) with -u flag
            # The test webserver responds with a number (webserver ID)
            # BusyBox nc auto-detects IPv6, no need for -6 flag
            # Pipe to head -n1 to read first line and close connection
            # Use short timeout (2s) for UDP as responses are immediate
            base_cmd = 'echo -n "GET" | nc -u -w 2 {} {} | head -n1'.format(
                vip_address, protocol_port)
        elif protocol in (const.HTTP, const.HTTPS, const.TCP):
            # For HTTP/HTTPS/TCP, use curl
            if ip_obj.version == 6:
                vip_url = 'http://[{}]:{}'.format(vip_address, protocol_port)
            else:
                vip_url = 'http://{}:{}'.format(vip_address, protocol_port)
            base_cmd = 'curl -g -s -S -m {} {}'.format(
                request_timeout, vip_url)
        else:
            raise Exception('Unsupported protocol for VM-based testing: '
                            '{}'.format(protocol))

        # Wait for LB to be functional first
        LOG.debug('Waiting for LB to be functional from VM...')
        max_wait_attempts = int(request_timeout / 2)
        for attempt in range(max_wait_attempts):
            try:
                result = ssh_client.exec_command(base_cmd)
                if result and result.strip():
                    LOG.debug('LB is functional from VM')
                    break
            except Exception:
                if attempt == max_wait_attempts - 1:
                    raise Exception(
                        'Timeout waiting for LB to be functional from VM')
                time.sleep(2)

        # Send requests to measure load balancing
        for i in range(repeat):
            try:
                result = ssh_client.exec_command(base_cmd)
                data = result.strip()

                if not data:
                    LOG.warning('Empty response from VIP on request %d', i)
                    failed_requests += 1
                    continue

                if data in response_counts:
                    response_counts[data] += 1
                else:
                    response_counts[data] = 1

                if delay is not None:
                    time.sleep(delay)
            except Exception as e:
                LOG.exception('Failed to send request %d to loadbalancer vip '
                              'from VM', i)
                failed_requests += 1
                if failed_requests > repeat * 0.2:  # Allow 20% failure rate
                    raise Exception(
                        'Too many failed requests to lb from VM: {}'.format(
                            e))
        LOG.debug('Loadbalancer response totals from VM: %s', response_counts)
        LOG.debug('Failed requests from VM: %d', failed_requests)

        # Ensure the correct number of members responded
        self.assertEqual(
            traffic_member_count, len(response_counts),
            'Expected {} members but got responses from {}'.format(
                traffic_member_count, len(response_counts)))

        # Check load balancing distribution based on algorithm
        if lb_algorithm == const.LB_ALGORITHM_ROUND_ROBIN:
            successful_requests = repeat - failed_requests
            if successful_requests % traffic_member_count == 0:
                # Requests should be evenly distributed
                expected_per_member = (successful_requests //
                                       traffic_member_count)
                for member, count in response_counts.items():
                    self.assertEqual(
                        expected_per_member, count,
                        'Expected {} requests per member but member '
                        'got {}'.format(expected_per_member, count))
            else:
                # Allow difference of 1 between members
                response_values = list(response_counts.values())
                max_diff = max(response_values) - min(response_values)
                self.assertLessEqual(
                    max_diff, 1,
                    'Request distribution variance too high: '
                    '{}'.format(response_counts))

    def assertConsistentResponse(self, response, url, method='GET', repeat=10,
                                 redirect=False, timeout=2,
                                 expect_connection_error=False, **kwargs):
        """Assert that a request to URL gets the expected response.

        :param response: Expected response in format (status_code, content).
        :param url: The URL to request.
        :param method: The HTTP method to use (GET, POST, PUT, etc)
        :param repeat: How many times to test the response.
        :param data: Optional data to send in the request.
        :param headers: Optional headers to send in the request.
        :param cookies: Optional cookies to send in the request.
        :param redirect: Is the request a redirect? If true, assume the passed
                         content should be the next URL in the chain.
        :param timeout: Optional seconds to wait for the server to send data.
        :param expect_connection_error: Should we expect a connection error
        :param expect_timeout: Should we expect a connection timeout

        :return: boolean success status

        :raises: testtools.matchers.MismatchError
        """
        session = requests.Session()
        response_code, response_content = response

        for i in range(repeat):
            if url.startswith(const.HTTP.lower()):
                if expect_connection_error:
                    self.assertRaises(
                        requests.exceptions.ConnectionError, session.request,
                        method, url, allow_redirects=not redirect,
                        timeout=timeout, **kwargs)
                    continue

                req = session.request(method, url,
                                      allow_redirects=not redirect,
                                      timeout=timeout, **kwargs)
                if response_code:
                    self.assertEqual(response_code, req.status_code)
                if redirect:
                    self.assertTrue(req.is_redirect)
                    self.assertEqual(response_content,
                                     session.get_redirect_target(req))
                elif response_content:
                    self.assertEqual(str(response_content), req.text)
            elif url.startswith(const.UDP.lower()):
                parsed_url = urlparse(url)
                if expect_connection_error:
                    self.assertRaises(exceptions.TimeoutException,
                                      self.make_udp_request,
                                      parsed_url.hostname,
                                      port=parsed_url.port, timeout=timeout)
                    continue

                data = self.make_udp_request(parsed_url.hostname,
                                             port=parsed_url.port,
                                             timeout=timeout)
                self.assertEqual(response_content, data)

    def _wait_for_lb_functional(
            self, vip_address, traffic_member_count, protocol_port, protocol,
            HTTPS_verify, client_cert_path=None, CA_certs_path=None,
            request_interval=2, request_timeout=10, requests_session=None,
            source_port=None):
        start = time.time()
        response_counts = {}

        # Send requests to the load balancer until at least
        # "traffic_member_count" members have replied (ensure network
        # connectivity is functional between the load balancer and the members)
        while time.time() - start < CONF.load_balancer.build_timeout:
            try:
                data = self.make_request(
                    vip_address, protocol=protocol, HTTPS_verify=HTTPS_verify,
                    protocol_port=protocol_port,
                    client_cert_path=client_cert_path,
                    CA_certs_path=CA_certs_path, source_port=source_port,
                    request_timeout=request_timeout,
                    requests_session=requests_session)

                if data in response_counts:
                    response_counts[data] += 1
                else:
                    response_counts[data] = 1

                if traffic_member_count == len(response_counts):
                    LOG.debug('Loadbalancer response totals: %s',
                              response_counts)
                    time.sleep(1)
                    return
            except Exception as e:
                LOG.warning('Server is not passing initial traffic. Waiting.')
                if "[Errno 98] Address already in use" in str(e):
                    raise e
            time.sleep(request_interval)

        LOG.debug('Loadbalancer wait for load balancer response totals: %s',
                  response_counts)
        message = ('Server %s on port %s did not begin passing traffic within '
                   'the timeout period. Failing test.' % (vip_address,
                                                          protocol_port))
        LOG.error(message)
        raise Exception(message)

    def make_udp_requests_with_retries(
            self, vip_address, number_of_retries, dst_port,
            src_port=None, socket_timeout=20):
        """Send UDP packets using retries mechanism

        The delivery of data to the destination cannot be guaranteed in UDP.
        In case when UDP package is getting lost and we might want to check
        what could be the reason for that (Network issues or Server Side),
        well need to send more packets to get into the conclusion.

        :param vip_address: LB VIP address
        :param number_of_retries: integer number of retries
        :param dst_port: UDP server destination port
        :param src_port: UDP source port to bind for UDP connection
        :param socket_timeout: UDP socket timeout
        :return: None if all UPD retries failed, else first successful
                 response data from UDP server.
        """
        retry_number = 0
        received_data = None
        while retry_number < number_of_retries:
            LOG.info('make_udp_requests_with_retries attempt number: %s',
                     retry_number)
            retry_number += 1
            try:
                received_data = self.make_udp_request(
                    vip_address, dst_port, timeout=socket_timeout,
                    source_port=src_port)
                break
            except Exception as e:
                LOG.warning('make_udp_request has failed with: %s', e)
        return received_data
