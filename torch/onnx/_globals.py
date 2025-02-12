"""Globals used internally by the ONNX exporter.

Do not use this module outside of `torch.onnx` and its tests.

Be very judicious when adding any new global variables. Do not create new global
variables unless they are absolutely necessary.
"""
from __future__ import annotations

import typing
from typing import Optional

# This module should only depend on _constants and nothing else in torch.onnx to keep
# dependency direction clean.
from torch.onnx import _constants

if typing.TYPE_CHECKING:
    # Postpone type checking to avoid circular dependencies.
    from torch.onnx import OperatorExportTypes, TrainingMode


class _InternalGlobals:
    """Globals used internally by ONNX exporter.

    NOTE: Be very judicious when adding any new variables. Do not create new
    global variables unless they are absolutely necessary.
    """

    def __init__(self):
        self._export_onnx_opset_version = _constants.onnx_default_opset
        self.operator_export_type: Optional[OperatorExportTypes] = None
        self.training_mode: Optional[TrainingMode] = None
        self.onnx_shape_inference: bool = False

    @property
    def export_onnx_opset_version(self):
        return self._export_onnx_opset_version

    @export_onnx_opset_version.setter
    def export_onnx_opset_version(self, value: int):
        supported_versions = [_constants.onnx_main_opset]
        supported_versions.extend(_constants.onnx_stable_opsets)
        if value not in supported_versions:
            raise ValueError(f"Unsupported ONNX opset version: {value}")
        self._export_onnx_opset_version = value


GLOBALS = _InternalGlobals()
