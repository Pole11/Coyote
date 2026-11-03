/**
 * This file is part of the Coyote <https://github.com/fpgasystems/Coyote>
 *
 * MIT Licence
 * Copyright (c) 2021-2025, Systems Group, ETH Zurich
 * All rights reserved.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:

 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.

 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include <iostream>
#include <cstdlib>
#include <fstream>
#include <string>
#include <csignal>

// External library for easier parsing of CLI arguments by the executable
#include <boost/program_options.hpp>

// Coyote-specific includes
#include <coyote/cThread.hpp>
#include <constants.hpp>

#include <arpa/inet.h>

constexpr bool const IS_CLIENT = false;

static volatile std::sig_atomic_t stop = 0;

// Note, how the Coyote thread is passed by reference; to avoid creating a copy of 
// the thread object which can lead to undefined behaviour and bugs. 
void run_bench(
    coyote::cThread &coyote_thread, coyote::rdmaSg &sg, 
    int *mem, uint transfers, uint n_runs, bool operation
) {
    // When writing, the server asserts the written payload is correct (which the client sets)
    // When reading, the client asserts the read payload is correct (which the server sets)
    for (int i = 0; i < sg.len / sizeof(int); i++) {
        mem[i] = operation ? 0 : i;        
    }

    for (int i = 0; i < n_runs; i++) {
        // Clear previous completion flags and sync with client
        coyote_thread.clearCompleted();
        coyote_thread.connSync(IS_CLIENT);

        // For writes, wait until client has written the targer number of messages; then write them back
        if (operation) {
            while (coyote_thread.checkCompleted(coyote::CoyoteOper::LOCAL_WRITE) != transfers) {}

            for (int i = 0; i < transfers; i++) {
                coyote_thread.invoke(coyote::CoyoteOper::REMOTE_RDMA_WRITE, sg);
            }
        // For reads, the server is completely passive 
        } else { 

        }
    }
    
    // Functional correctness check
    if (operation) {
        for (int i = 0; i < sg.len / sizeof(int); i++) {
            assert(mem[i] == i);                        
        }
    }
}

int main(int argc, char *argv[])  {
    // CLI arguments
    unsigned int min_size, max_size;
    std::string peer_ip;

    boost::program_options::options_description runtime_options("Coyote Perf RDMA Options");
    runtime_options.add_options()
        ("min_size,x", boost::program_options::value<unsigned int>(&min_size)->default_value(MIN_TRANSFER_SIZE_DEFAULT), "Starting (minimum) transfer size")
        ("max_size,X", boost::program_options::value<unsigned int>(&max_size)->default_value(MAX_TRANSFER_SIZE_DEFAULT), "Ending (maximum) transfer size")
        ("peer_ip,p", boost::program_options::value<std::string>(&peer_ip)->default_value(""), "Host IP acting as a fake RDMA peer (RX test)");
    boost::program_options::variables_map command_line_arguments;
    boost::program_options::store(boost::program_options::parse_command_line(argc, argv, runtime_options), command_line_arguments);
    boost::program_options::notify(command_line_arguments);

    std::cout << "-- CLI PARAMETERS: --" << std::endl;
    std::cout << "Starting transfer size: " << min_size << std::endl;
    std::cout << "Ending transfer size: " << max_size << std::endl;
    std::cout << "Peer you are listening to: " << peer_ip << std::endl << std::endl;

    // Allocate Coyothe threa and set-up RDMA connections, buffer etc.
    // initRDMA is explained in more detail in client/main.cpp
    coyote::cThread coyote_thread(DEFAULT_VFPGA_ID, getpid());
    if (!peer_ip.empty()) {
        int *mem = (int *) coyote_thread.getMem({coyote::CoyoteAllocType::HPF, max_size, true});
        printf("Allocated Huge Pages at = %p\n\n", (void *) mem);
        //int *mem = (int *) coyote_thread.getMem({coyote::CoyoteAllocType::REG, max_size, true});
        //printf("Allocated Normal Pages at = %p\n\n", (void *) mem);

        coyote::ibvQp *qp = coyote_thread.getQpair();

        qp->remote.ip_addr = ntohl(inet_addr(peer_ip.c_str()));
        for (int i = 0; i < 32; i += 8) qp->remote.uintToGid(i, qp->remote.ip_addr);
        qp->remote.qpn = 0x100;
        qp->remote.psn = 0;
        qp->remote.rkey = 0;
        qp->remote.vaddr = nullptr;

        coyote_thread.writeQpContext(coyote::DEF_PORT);
        coyote_thread.doArpLookup(qp->remote.ip_addr);
        coyote_thread.doArpLookup(0xC0A80101); 
        std::cout << "-- QP PARAMETERS: --" << std::endl;
        printf("QPN: 0x%x\nPSN: 0x%x\nVADDR: 0x%lx\n\n", qp->local.qpn, qp->local.psn, (uint64_t) qp->local.vaddr);
        
        auto counter = [](const std::string &label) {
            std::ifstream f("/sys/kernel/coyote_sysfs_0/cyt_attr_nstats");
            std::string line;
            while (std::getline(f, line)) if (line.rfind(label, 0) == 0) return std::stoul(line.substr(label.size()));
            return 0UL;
        };
        std::signal(SIGINT, [](int) { stop = 1; });
        
        unsigned long rx = counter("IBV RX pkgs:"), drop = counter("PSN drop cnt:");
        
        std::cout << "Run this on the computer that emulates the requestor:" << std::endl;
        printf("python3 injector.py 0x%x 0x%x 0x%lx %d\n\n", qp->local.qpn, qp->local.psn, (uint64_t) qp->local.vaddr, (int) coyote_thread.checkCompleted(coyote::CoyoteOper::LOCAL_WRITE));
        
        while (!stop) {
            //usleep(100000);
            unsigned long new_rx = counter("IBV RX pkgs:"), new_drop = counter("PSN drop cnt:");
            if (new_rx == rx) continue;

            std::cout << "Received " << new_rx - rx << " packets, " << new_drop - drop << " dropped (duplicate or out of window)" << std::endl;            
            std::cout << "WRITE messages completed so far: " << coyote_thread.checkCompleted(coyote::CoyoteOper::LOCAL_WRITE) << std::endl;
            qp->local.psn = (qp->local.psn + (new_rx - rx) - (new_drop - drop)) & 0xFFFFFF;
            std::cout << "This is memory after the packet arrived:" << std::endl;
            
            uint8_t *b = (uint8_t *) mem;
            for (unsigned int i = 0; i < max_size; i += 32) {
                bool used = false;
                for (unsigned int k = i; k < i + 32 && k < max_size; k++) used |= b[k] != 0;
                if (!used) continue;
                printf("%5u:", i);
                for (unsigned int k = i; k < i + 32 && k < max_size; k++) printf(" %02x", b[k]);
                printf("\n");
            }
            
            std::cout << std::dec << std::endl;
            std::cout << "Run this on the computer that emulates the requestor:" << std::endl;
            printf("python3 injector.py 0x%x 0x%x 0x%lx %d\n\n", qp->local.qpn, qp->local.psn, (uint64_t) qp->local.vaddr, (int) coyote_thread.checkCompleted(coyote::CoyoteOper::LOCAL_WRITE));
            
            rx = new_rx;
            drop = new_drop;
        }
        return EXIT_SUCCESS;
    } else {
        std::cout << "Please provide the peer address! It must be the IP address of the peer you want to receive packets from, if the computer that is emulating the requestor has the output interface with 192.168.88.254 as the IP, then run this on that machine " << std::endl << "./test -p 192.168.88.254 -X 4096" << std::endl;
        return EXIT_SUCCESS;
    }
    
}
