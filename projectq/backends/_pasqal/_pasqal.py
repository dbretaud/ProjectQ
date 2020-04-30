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
""" Back-end to run quantum program on pasqal's API."""

import math
import random

from projectq.cengines import BasicEngine
from projectq.meta import get_control_count, LogicalQubitIDTag
from projectq.ops import (Rx, Ry, Rz, CZ, Z, Measure, Allocate, Barrier, Deallocate,
                          FlushGate)

from ._pasqal_http_client import send, retrieve


# _rearrange_result & _format_counts imported and modified from qiskit
def _rearrange_result(input_result, length):
    bin_input = list(bin(input_result)[2:].rjust(length, '0'))
    return ''.join(bin_input)[::-1]


def _format_counts(samples, length):
    counts = {}
    for result in samples:
        h_result = _rearrange_result(result, length)
        if h_result not in counts:
            counts[h_result] = 1
        else:
            counts[h_result] += 1
    counts = {
        k: v
        for k, v in sorted(counts.items(), key=lambda item: item[0])
    }
    return counts


class PasqalBackend(BasicEngine):
    """
    The pasqal Backend class, which stores the circuit, transforms it to the
    appropriate data format, and sends the circuit through the pasqal API.
    """
    def __init__(self,
                 num_runs=100,
                 verbose=False,
                 token='',
                 device='pasqal_simulator',
                 num_retries=3000,
                 interval=1,
                 retrieve_execution=None):
        """
        Initialize the Backend object.

        Args:
            use_hardware (bool): If True, the code is run on the pasqal quantum
                chip (instead of using the pasqal simulator)
            num_runs (int): Number of runs to collect statistics.
                (default is 100, max is usually around 200)
            verbose (bool): If True, statistics are printed, in addition to
                the measurement result being registered (at the end of the
                circuit).
            token (str): pasqal user API token.
            device (str): name of the pasqal device to use. simulator By default
            num_retries (int): Number of times to retry to obtain
                results from the pasqal API. (default is 3000)
            interval (float, int): Number of seconds between successive
                attempts to obtain results from the pasqal API.
                (default is 1)
            retrieve_execution (int): Job ID to retrieve instead of re-
                running the circuit (e.g., if previous run timed out).
        """
        BasicEngine.__init__(self)
        self._reset()
        self.device = 'pasqal_simulator'
        self._clear = True
        self._num_runs = num_runs
        self._verbose = verbose
        self._token = token
        self._num_retries = num_retries
        self._interval = interval
        self._probabilities = dict()
        self._circuit = []
        self._mapper = []
        self._measured_ids = []
        self._allocated_qubits = set()
        self._retrieve_execution = retrieve_execution

    def is_available(self, cmd):
        """
        Return true if the command can be executed.

        The pasqal ion trap can only do Rx,Ry and Rxx.

        Args:
            cmd (Command): Command for which to check availability
        """
        print('is_available_function')
        if get_control_count(cmd) == 0:
            if isinstance(cmd.gate, (Rx, Ry, Rz)):
                return True
        if get_control_count(cmd) ==1:
            print('gooooood 2qg?')
            print(cmd)
            if isinstance(cmd.gate, (CZ,)):
                print('yes')
                return True
        if cmd.gate in (Measure, Allocate, Deallocate):
            return True
        print('wrong gate')
        print(cmd)
        return False

    def _reset(self):
        """ Reset all temporary variables (after flush gate). """
        self._clear = True
        self._measured_ids = []

    def _store(self, cmd):
        """
        Temporarily store the command cmd.

        Translates the command and stores it in a local variable (self._cmds).

        Args:
            cmd: Command to store
        """
        if self._clear:
            self._probabilities = dict()
            self._clear = False
            self._buffer_sq_id=dict()
            self._circuit = []
            self._circuitmockup = []
            self._allocated_qubits = set()

        gate = cmd.gate
        if gate == Allocate:
            self._allocated_qubits.add(cmd.qubits[0][0].id)
            self._buffer_sq_id[cmd.qubits[0][0].id]=[]
            return
        if gate == Deallocate:
            return
        if gate == Measure:
            assert len(cmd.qubits) == 1 and len(cmd.qubits[0]) == 1
            qb_id = cmd.qubits[0][0].id
            self._buffer_sq_id[qb_id]=-1
            logical_id = None
            for tag in cmd.tags:
                if isinstance(tag, LogicalQubitIDTag):
                    logical_id = tag.logical_qubit_id
                    break
            # assert logical_id is not None
            if logical_id is None:
                logical_id = qb_id
                self._mapper.append(qb_id)
            self._measured_ids += [logical_id]
            return
        elif gate == Z and get_control_count(cmd) == 1:
            new_moment,moment_number=self._get_moment(gate,qubit)
            self._buffer_sq_id[cmd.control_qubits[0].id].append([moment_number,gate])
            self._buffer_sq_id[cmd.qubits[0][0].id].append([moment_number,gate])
            ids = [cmd.control_qubits[0].id, cmd.qubits[0][0].id]
            instruction = self._get_2qg_moment_json(ids)
            self._circuit.append(instruction)
        if isinstance(gate, (Rx, Ry, Rz)):
            qubit = cmd.qubits[0][0].id
            instruction = []
            new_moment,moment_number=self._get_moment(gate,qubit)
            self._buffer_sq_id[qubit].append([moment_number,gate])
            if new_moment:
                instruction=self._get_1qg_moment_json(gate,qubit)
                self._circuit.append(instruction)
            else:
                pm=self._circuit[moment_number]
                instruction=self._add_1qg_moment_json(pm,gate,qubit)
                self._circuit[moment_number]=instruction
            return
        return
        #raise Exception('Invalid command: ' + str(cmd))

#TODO handle both single and multiple ids
#TODO WTF WITH PARALLEL OPS????
    def _get_moment(self,gate,id,parallel=False):
        if not parallel:
            return True,len(self._circuit)
        else:
            if len(self._buffer_sq_id)==0:
                guessed_moment=0
            else:
                guessed_moment=self._buffer_sq_id[qubit][-1][0]+1
            if len(self._circuitmockup)>guessed_moment:
                return False,guessed_moment
            else:
                return True,len(self._circuit)


    def _logical_to_position(self,qb_id,row=3,column=3,layer=1):
        """
        Return the json string of the atom position with the given logical id.
        Assumes a 3Dgrid

        Args:
            qb_id (int): ID of the logical qubit whose position should be
                returned.
        """
        qb_ph=qb_id#self._logical_to_physical(qb_id)
        ph_row=int(qb_ph % row)
        ph_col=int(int(qb_ph/row)%column)
        ph_lay=int(int(qb_ph/(row*column))%layer)
        return_json={
              "cirq_type": "ThreeDGridQubit",
              "row": ph_row,
              "col": ph_col,
              "lay": ph_lay
        }
        return return_json

    def _get_measurement_json(self,ids):
        return_json={
            "cirq_type": "Moment",
            "operations": [
                {
                "cirq_type": "GateOperation",
                "gate": {
                    "cirq_type": "MeasurementGate",
                    "num_qubits": len(ids),
                    "key": "x",
                    "invert_mask": []
                },
                "qubits": [self._logical_to_position(i) for i in ids]
                }
            ]
        }
        return return_json

    def _get_device_json(self,size):
        return_json={
            "cirq_type": "PasqalDevice",
            "control_radius": 2.5,
            "qubits": [self._logical_to_position(i) for i in range(size)]
        }
        return return_json

    def _get_1qg_moment_json(self,gate,id):
        u_name = {'Rx': "X", 'Ry': "Y", 'Rz': "Z"}
        angle = round(gate.angle / math.pi,2)
        return_json={
            "cirq_type": "Moment",
            "operations": [
                {
                "cirq_type": "GateOperation",
                "gate": {
                    "cirq_type": u_name[str(gate)[0:2]]+"PowGate",
                    "exponent": angle,
                    "global_shift": 0
                },
                "qubits": [
                    self._logical_to_position(id)
                  ]
                }
            ]
        }
        return return_json

    def _add_1qg_moment_json(self,moment,gate,id):
        u_name = {'Rx': "X", 'Ry': "Y", 'Rz': "Z"}
        angle = round(gate.angle / math.pi,2)
        new_op={
                "cirq_type": "GateOperation",
                "gate": {
                    "cirq_type": u_name[str(gate)[0:2]]+"PowGate",
                    "exponent": angle,
                    "global_shift": 0
                },
                "qubits": [
                    self._logical_to_position(id)
                  ]
                }
        moment["operations"].append(new_op)
        return moment

    def _get_2qg_moment_json(self,ids):
        return_json={
            "cirq_type": "Moment",
            "operations": [
                {
                "cirq_type": "GateOperation",
                "gate": {
                    "cirq_type": "CZPowGate",
                    "exponent": 1,
                    "global_shift": 0
                },
                "qubits": [
                    self._logical_to_position(id) for id in ids
                  ]
                }
            ]
        }
        return return_json
        
    def _add_2qg_moment_json(self,moment,ids):
        new_op={
                "cirq_type": "GateOperation",
                "gate": {
                    "cirq_type": "CZPowGate",
                    "exponent": 1,
                    "global_shift": 0
                },
                "qubits": [
                    self._logical_to_position(id) for id in ids
                  ]
                }
        moment["operations"].append(new_op)
        return moment

    def _logical_to_physical(self, qb_id):
        """
        Return the physical location of the qubit with the given logical id.
        If no mapper is present then simply returns the qubit ID.

        Args:
            qb_id (int): ID of the logical qubit whose position should be
                returned.
        """
        try:
            mapping = self.main_engine.mapper.current_mapping
            if qb_id not in mapping:
                raise RuntimeError(
                    "Unknown qubit id {}. Please make sure "
                    "eng.flush() was called and that the qubit "
                    "was eliminated during optimization.".format(qb_id))
            return mapping[qb_id]
        except AttributeError:
            if qb_id not in self._mapper:
                print('LOGICAL_TO_PHYSICAL_GET_MAPPER')
                print(self._mapper)
                raise RuntimeError(
                    "Unknown qubit id {}. Please make sure "
                    "eng.flush() was called and that the qubit "
                    "was eliminated during optimization.".format(qb_id))
            return qb_id

    def get_probabilities(self, qureg):
        """
        Return the list of basis states with corresponding probabilities.
        If input qureg is a subset of the register used for the experiment,
        then returns the projected probabilities over the other states.
        The measured bits are ordered according to the supplied quantum
        register, i.e., the left-most bit in the state-string corresponds to
        the first qubit in the supplied quantum register.
        Warning:
            Only call this function after the circuit has been executed!
        Args:
            qureg (list<Qubit>): Quantum register determining the order of the
                qubits.
        Returns:
            probability_dict (dict): Dictionary mapping n-bit strings to
            probabilities.
        Raises:
            RuntimeError: If no data is available (i.e., if the circuit has
                not been executed). Or if a qubit was supplied which was not
                present in the circuit (might have gotten optimized away).
        """
        if len(self._probabilities) == 0:
            raise RuntimeError("Please, run the circuit first!")

        probability_dict = dict()
        for state in self._probabilities:
            mapped_state = ['0'] * len(qureg)
            for i, qubit in enumerate(qureg):
                mapped_state[i] = state[self._logical_to_physical(qubit.id)]
            probability = self._probabilities[state]
            mapped_state = "".join(mapped_state)

            probability_dict[mapped_state] = (
                probability_dict.get(mapped_state, 0) + probability)
        return probability_dict

    def _run(self):
        """
        Run the circuit.

        Send the circuit via the pasqal API using the provided user
        token / ask for the user token.
        """
        # finally: measurements
        # NOTE pasqal DOESN'T SEEM TO HAVE MEASUREMENT INSTRUCTIONS (no
        # intermediate measurements are allowed, so implicit at the end)
        # return if no operations.
        if self._circuit == []:
            return

        self._circuit.append(self._get_measurement_json(self._measured_ids))
        n_qubit = max(self._allocated_qubits) + 1
        info = {}
        cirq_json={
            "cirq_type": "Circuit",
            "moments": [moment for moment in self._circuit],
            "device": self._get_device_json(n_qubit)
        }
        print('CIRQ_JSON')
        cirq_json=str(cirq_json).replace("'", '"')
        print(cirq_json)
        info['circuit'] = cirq_json
        info['nq'] = n_qubit
        info['shots'] = self._num_runs
        info['backend'] = {'name': self.device}
        if self._num_runs > 1024:
            raise Exception("Number of shots limited unknwon. Max put at 1024 by default")
        try:
            if self._retrieve_execution is None:
                res = send(info,
                           device=self.device,
                           token=self._token,
                           shots=self._num_runs,
                           num_retries=self._num_retries,
                           interval=self._interval,
                           verbose=self._verbose)
            else:
                res = retrieve(device=self.device,
                               token=self._token,
                               jobid=self._retrieve_execution,
                               num_retries=self._num_retries,
                               interval=self._interval,
                               verbose=self._verbose)
            self._num_runs = len(res)
            counts = _format_counts(res, n_qubit)
            # Determine random outcome
            P = random.random()
            p_sum = 0.
            measured = ""
            for state in counts:
                probability = counts[state] * 1. / self._num_runs
                p_sum += probability
                star = ""
                if p_sum >= P and measured == "":
                    measured = state
                    star = "*"
                self._probabilities[state] = probability
                if self._verbose and probability > 0:
                    print(str(state) + " with p = " + str(probability) + star)

            class QB():
                def __init__(self, qubit_id):
                    self.id = qubit_id

            # register measurement result
            for qubit_id in self._measured_ids:
                location = self._logical_to_physical(qubit_id)
                result = int(measured[location])
                self.main_engine.set_measurement_result(QB(qubit_id), result)
            self._reset()
        except TypeError:
            raise Exception("Failed to run the circuit. Aborting.")

    def receive(self, command_list):
        """
        Receives a command list and, for each command, stores it until
        completion.

        Args:
            command_list: List of commands to execute
        """
        for cmd in command_list:
            if not isinstance(cmd.gate, FlushGate):
                self._store(cmd)
            else:
                self._run()
                self._reset()
