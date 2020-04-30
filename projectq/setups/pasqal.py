#   Copyright 2020 ProjectQ-Framework (www.projectq.ch)
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
"""
Defines a setup allowing to compile code for the Pasqal neutral atom simulator:

It provides the `engine_list` for the `MainEngine' based on arbibtrary parameters
for the simulator.  
Decompose the circuit into a Rx/Ry/Cz gate set.
"""

import projectq
import projectq.setups.decompositions
from projectq.setups import restrictedgateset
from projectq.ops import (Rx, Ry, Cz, Barrier)
from projectq.cengines import (LocalOptimizer, IBM5QubitMapper,
                               SwapAndCNOTFlipper, BasicMapperEngine,
                               GridMapper)
from projectq.backends._psaqal._aqt_http_client import show_devices


def get_engine_list(token=None, device=None):
    # Access to the hardware properties via show_devices
    # Can also be extended to take into account gate fidelities, new available
    # gate, etc..
    devices = show_devices(token)
    pasqal_setup = []
    if device not in devices:
        raise DeviceOfflineError('Error when configuring engine list: device '
                                 'requested for Backend not connected')
    if device == 'pasqal_simulator':
        #TODO: need to handle 3d grids
        mapper = BasicMapperEngine()
        # Note: Manual Mapper doesn't work, because its map is updated only if
        # gates are applied if gates in the register are not used, then it
        # will lead to state errors
        res = dict()
        for i in range(devices[device]['nq']):
            res[i] = i
        mapper.current_mapping = res
        pasqal_setup = [mapper]
    else:
        # If there is an online device not handled into ProjectQ it's not too
        # bad, the engine_list can be constructed manually with the
        # appropriate mapper and the 'coupling_map' parameter
        raise DeviceNotHandledError('Device not yet fully handled by ProjectQ')

    # Most gates need to be decomposed into a subset that is manually converted
    # in the backend (until the implementation of the U1,U2,U3)
    setup = restrictedgateset.get_engine_list(one_qubit_gates=(Rx, Ry),
                                              two_qubit_gates=(Cz,),
                                              other_gates=(Barrier, ))
    setup.extend(pasqal_setup)
    return setup


class DeviceOfflineError(Exception):
    pass


class DeviceNotHandledError(Exception):
    pass


