# Copyright 2023 Red Hat
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

from tempest.lib.common.utils import misc


@misc.singleton
class PortNumberIterator(object):
    def __init__(self, first_port=80):
        self.current_port = first_port

    def __iter__(self):
        return self

    def __next__(self):
        port_number = self.current_port
        self.current_port += 1
        return port_number
