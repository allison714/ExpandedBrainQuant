"""ebq: 3D analysis of pan-ExM volumes.

Modules
  io.precomputed   Neuroglancer precomputed volumes read in 3D blocks (pan-ASLM output)
  io.zarr_ome      OME-Zarr (bioformats2raw layout) stacks
  manifest         which channel is the pan stain / which antibody, animal, genotype, sex
  profile          the DP-PSD profile measurement (two-Gaussian gate, peak-to-peak reading)
  synapse3d        synapses found as bright plates in a 3D pan-stain block
  nuclei           nuclei found as smooth regions in a 3D pan-stain volume; sizes for an ExF ruler
"""

__version__ = "0.1.0"
