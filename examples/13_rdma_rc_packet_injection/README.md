# Coyote Example 13: Injecting RDMA RC Packets via software

This example has the goal to test the RX path of Coyote.

First of all compile the hardware as usual:

```bash
cd hw/

mkdir build_hw && cd build_hw                
cmake ../ -DFDEV_NAME=<target_dev>     
make project && make bitgen
```

Note that `target_dev` can be `u55c`, `u250`, `u280`, `v80`, ...

Then compile the driver as usual:

```bash
make TARGET_PLATFORM=<versal|ultrascale_plus>
```

You start by compiling the software:

```bash
cd sw/

mkdir build_server && cd build_server
cmake ../ -DINSTANCE=server && make
```

Then run the software, you should specify the IP of the peer you want to receive packets from with `-p` flag, the other options are similar to the ones of example 09 (`perf_rdma`).

Depending on the version of the software, it may look different, but the essence is something like this:

```
Run this on the computer that emulates the requestor:
python3 injector.py 0x0 0x3fdbc7 0x78cd79200000 
```

You should copy and paste the python command in the machine you want to send packets from (the one you specify the IP of the interface with the `-p` flag before).
