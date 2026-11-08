#!/bin/bash -x


  # Brief single-thread test: check light-load turbo behavior
  stress-ng --cpu 1 --cpu-method matrixprod --timeout 2m --metrics-brief

  # Initial all-thread cooling and power check
  stress-ng --cpu 0 --cpu-method matrixprod --timeout 5m --metrics-brief

  # Sustained load: let the heatsink, case and VRMs heat up
  stress-ng --cpu 0 --cpu-method matrixprod --timeout 45m --metrics-brief

  # Broader CPU exercise with verification where supported
  stress-ng --cpu 0 --cpu-method all --verify --timeout 30m --metrics-brief


