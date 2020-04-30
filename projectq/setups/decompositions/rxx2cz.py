#   Copyright 2018 ProjectQ-Framework (www.projectq.ch)
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
Registers a decomposition to for a RXX gate in terms of CZ, Rx and Ry gates.
"""

from projectq.cengines import DecompositionRule
from projectq.meta import get_control_count
from projectq.ops import Ph, Rxx, Ry, Rx, X, CZ, H
import math


def _decompose_rxx2cz(cmd):
    """ Decompose RXX gate into CZ gate.
    Note: Have not been optimized at all. """
    Rx(math.pi / 2) | cmd.qubits[0][0]
    Ph(-7*math.pi / 4) | cmd.qubits[0][0]
    Ry(-math.pi / 2) | cmd.qubits[0][0]
    Rx(math.pi / 2) | cmd.qubits[1][0]
    H | cmd.qubits[1][0]
    CZ | (cmd.qubits[0][0],cmd.qubits[1][0])
    Ry(math.pi / 2) | cmd.qubits[0][0]
    H | cmd.qubits[1][0]

def _recognize_rxx(cmd):
    """ Identify that the command is a Rxx gate"""
    return get_control_count(cmd) == 0


#: Decomposition rules
all_defined_decomposition_rules = [
    DecompositionRule(Rxx, _decompose_rxx2cz, _recognize_rxx),
]
