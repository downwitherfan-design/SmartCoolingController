"""Simulation subsystem (spec §52, §314).

The simulation back-end is the only source of synthetic telemetry.  Importing
this package never affects real-hardware mode.
"""

from app.simulation.backend import SimulationBackend, SimulationKnobs

__all__ = ["SimulationBackend", "SimulationKnobs"]
