# Owner(s): ["oncall: distributed"]

import sys
from contextlib import suppress

import torch
import torch.distributed as dist
import torch.nn as nn
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
from torch.testing._internal.common_distributed import (
    skip_if_lt_x_gpu,
)
from torch.testing._internal.common_fsdp import (
    FSDPTest,
    NestedWrappedModule,
    FSDPInitMode,
)
from torch.testing._internal.common_utils import (
    instantiate_parametrized_tests,
    parametrize,
    TEST_WITH_DEV_DBG_ASAN,
    run_tests,
)

if not dist.is_available():
    print("Distributed not available, skipping tests", file=sys.stderr)
    sys.exit(0)

if TEST_WITH_DEV_DBG_ASAN:
    print(
        "Skip dev-asan as torch + multiprocessing spawn have known issues",
        file=sys.stderr,
    )
    sys.exit(0)


class TestFSDPMisc(FSDPTest):
    @property
    def world_size(self):
        return 2

    @property
    def process_group(self):
        return dist.distributed_c10d._get_default_group()

    @skip_if_lt_x_gpu(2)
    @parametrize("use_index", [True, False])
    def test_fsdp_device_id(self, use_index):
        """
        If CPU module is passed into FSDP with device_id
        argument, it is moved to the GPU with that device_id.
        """
        dev_id = (
            torch.cuda.current_device() if use_index
            else torch.device("cuda", torch.cuda.current_device())
        )

        def _check_device_matches(fsdp, dev_id):
            devices = {p.device for p in fsdp.parameters()}
            self.assertEqual(1, len(devices))
            found_dev = devices.pop()
            if use_index and not isinstance(dev_id, torch.device):
                dev_id = torch.device("cuda", dev_id)
            self.assertEqual(found_dev, dev_id)

        mod = NestedWrappedModule(
            group=self.process_group,
            wrap_fsdp=True,
            wrap_everything=True,
            fsdp_init_mode=FSDPInitMode.CUDA_NEVER,
            device_id=dev_id
        )
        fsdp = FSDP(mod, device_id=dev_id)
        # Check FSDP parameters are moved.
        _check_device_matches(fsdp, dev_id)
        # device_id matching module device before FSDP construction
        # should not throw errors.
        mod = NestedWrappedModule(
            group=self.process_group,
            wrap_fsdp=True,
            wrap_everything=True,
            fsdp_init_mode=FSDPInitMode.CUDA_BEFORE,
            device_id=dev_id
        )
        fsdp = FSDP(mod, device_id=dev_id)
        _check_device_matches(fsdp, dev_id)
        # Passing in torch.device("cuda") should work.
        regex = "does not have explicit index"
        context = self.assertWarnsRegex(
            expected_warning=UserWarning, expected_regex=regex
        )
        with context:
            mod = NestedWrappedModule(
                group=self.process_group,
                wrap_fsdp=True,
                wrap_everything=True,
                fsdp_init_mode=FSDPInitMode.CUDA_BEFORE,
                device_id=torch.device("cuda")
            )
            fsdp = FSDP(mod, device_id=torch.device("cuda"))
        _check_device_matches(fsdp, torch.device("cuda", torch.cuda.current_device()))

    @skip_if_lt_x_gpu(2)
    def test_module_device_mismatches_device_id(self):
        """
        FSDP raises errors when module is on a GPU that does
        not match device_id.
        """
        context = (
            self.assertRaisesRegex(
                RuntimeError,
                f"on rank {self.rank}.*cuda:0, but is on cuda:{self.rank}"
            ) if self.rank != 0 else suppress()
        )
        with context:
            mod = NestedWrappedModule(
                group=self.process_group,
                wrap_fsdp=True,
                wrap_everything=True,
                # Would move module to current cuda device before
                # wrapping with FSDP
                fsdp_init_mode=FSDPInitMode.CUDA_BEFORE,
                # Rank 1 is given device id 0, but model is on cuda:1,
                # should throw errors.
                device_id=0
            )

    @skip_if_lt_x_gpu(2)
    def test_multi_device_not_supported(self):
        """
        FSDP throws appropriate error when we wrap multi-device module.
        """
        class MyModule(nn.Module):
            def __init__(self):
                super().__init__()
                self.a = nn.Linear(1, 1).cuda()
                self.b = nn.Linear(1, 1)

        with self.assertRaisesRegex(
            RuntimeError, "FSDP only supports single device modules"
        ):
            FSDP(MyModule())

    @skip_if_lt_x_gpu(2)
    def test_no_params(self):
        """
        Test that device_id and cpu init work if module has no params
        (they are effective noops, but ensure FSDP does not assume module
        has parameters during init)
        """
        # Test CPU
        no_params = nn.ReLU()
        module = FSDP(no_params)
        # Test CUDA
        no_params = nn.ReLU().cuda()
        module = FSDP(no_params)
        # Test CPU + device_id
        no_params = nn.ReLU()
        module = FSDP(no_params, device_id=torch.cuda.current_device())
        # For modules with no params, wrong device_id will raise error about
        # inconsistency between compute_device and device_id, since compute_device
        # is computed as torch.cuda.current_device when there are no params.
        no_params = nn.ReLU().cuda()
        context = (
            self.assertRaisesRegex(
                AssertionError,
                f"Inconsistent.*cuda:{self.rank} vs cuda:0"
            )
        ) if self.rank != 0 else suppress()
        with context:
            module = FSDP(no_params, device_id=0)

    @skip_if_lt_x_gpu(2)
    def test_fsdp_cpu_init_stays_on_cpu(self):
        """
        Ensure that CPU model input stays on CPU
        after FSDP init even though sharding, flattening
        is run on GPU.
        """
        torch.cuda.set_device(self.rank)
        regex = "Module is input on CPU"
        context = self.assertWarnsRegex(
            expected_warning=UserWarning, expected_regex=regex
        )
        with context:
            mod = NestedWrappedModule(
                group=self.process_group,
                wrap_fsdp=True,
                wrap_everything=True,
                fsdp_init_mode=FSDPInitMode.CUDA_NEVER,
            )
            fsdp = FSDP(mod)
        devices = {p.device for p in fsdp.parameters()}
        self.assertEqual(1, len(devices))
        self.assertEqual(torch.device("cpu"), devices.pop())
        fsdp = fsdp.cuda()
        # Ensure fwd + backward can be performed after moving to CUDA.
        # CPU input also tests that input is correctly moved to appropriate
        # CUDA device.
        inp = mod.get_input(device=torch.device("cpu"))
        fsdp(inp[0]).sum().backward()

instantiate_parametrized_tests(TestFSDPMisc)

if __name__ == "__main__":
    run_tests()
