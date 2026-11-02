import socket, struct, sys, time, random

FPGA_IP = "192.168.1.2" 			# c0a80102, change it according to your setup
PEER_QPN = 0x100         			# qp->remote.qpn set in the -p block, the FPGA sends its replies to this QPN 
									# only useful to check that Coyote sent to the correct QPN
OUTPUT_NET_DEV = b"enxd0c0bf267d20"	# change it according to your setup

# receive parameters from argv
qpn, psn, vaddr = (int(x, 0) for x in sys.argv[1:4])

ops = ["write", "write", "write"] # read seem to not be working

sending_packets = []
real_received_packet = []
expected_received_packet = []

# > = big endian
# B = 1 Byte
# H = 2 Bytes
# I = 4 Bytes
# Q = 8 Bytes

def create_bth(opcode, psn):
	bth = struct.pack(">BBHII", 			# format
		opcode, 							# opcode
		0, 									# flag byte
		0xFFFF, 							# partition key
		qpn, 								# destination qpn
		(1 << 31) | psn)					# ackreq + psn
	return bth

def create_reth(vaddr):
	reth = struct.pack(">QII", 
		vaddr, 					# vaddr
		0, 						# rkey (coyote does not check it!)
		64)						# dma length (how many bytes to write/read)
	return reth
	
def create_payload():
	payload = bytes(range(64)) if op == "write" else b""
	return payload
	
def create_rdma_write_packet(psn, addr, data):
	packet = create_bth(0x0A, psn) + create_reth(addr) + data + bytes(4)
	return packet
	
def create_rdma_read_packet(psn, addr):
	packet = create_bth(0x0C, psn) + create_reth(addr) + bytes(4)
	return packet

def create_udp_socket():
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, OUTPUT_NET_DEV)
	s.bind(("", 4791))
	s.settimeout(2)
	
	return s
	
def wait(seconds=3):
    for i in range(1, seconds + 1):
        print(i, "...")
        time.sleep(1)
	
def send_packets(my_socket):
	for packet in sending_packets:
		my_socket.sendto(packet, (FPGA_IP, 4791))
		#time.sleep(0.005) # change it based, I am keeping it high to test functionality
		try:
			while True:
				reply, src = my_socket.recvfrom(65535)
				real_received_packet.append(reply)
		except TimeoutError:
			pass
	
def print_reply_and_check():
	replies = {struct.unpack(">I", r[8:12])[0] & 0xFFFFFF: r for r in real_received_packet} # dict to map replies to PSN
	for i, expected in enumerate(expected_received_packet):
		print("Checking", i)
		reply = replies.pop(expected[2], b"")
		if not reply:
			print("  BAD no reply")
			continue
		opcode, _, _, r_qpn, r_psn = struct.unpack(">BBHII", reply[:12])
		aeth, = struct.unpack(">I", reply[12:16])
		received = (opcode, r_qpn & 0xFFFFFF, r_psn & 0xFFFFFF, aeth >> 29, reply[16:-4])
		for name, e, r in zip(("opcode", "qpn", "psn", "ack type", "payload"), expected, received):
			e, r = (e.hex() or "none", r.hex() or "none") if name == "payload" else (hex(e), hex(r))
			print("  OK " if e == r else "  BAD", name, "expected", e, "received", r)
		print("  syndrome", hex(aeth >> 24), "msn", hex(aeth & 0xFFFFFF))
	for psn, reply in replies.items():
		print("Unexpected reply psn", hex(psn), "opcode", hex(reply[0]), "syndrome", hex(reply[12]))
			
# --- "MAIN" ---
print("+++ Standard Coyote Test +++\n")

# populate sending_packets and expected_received_packet
print("--- Populating sending_packets and expected_received_packet ---\n")
run_id = random.randint(1, 255)
print("The run id for this \"burst\" of packets is ", hex(run_id))
for i, op in enumerate(ops):
	print("Building packet ", i)
	p = (psn + i) & 0xFFFFFF
	if op == "write":
		# first write writes 01 run_id#1 01 run_id#1 01...
		# second write writes 02 run_id#2 02 run_id#2 02...
		sending_packets.append(create_rdma_write_packet(p, vaddr + i * 64, bytes([i + 1, run_id]) * 32)) 
		expected_received_packet.append((0x11, PEER_QPN, p, 0, b""))

print("")

print("--- Creating UDP Socket ---\n")
my_socket = create_udp_socket()
print("--- Sending Packets ---\n")
send_packets(my_socket)
print("--- Checking ---\n")
print_reply_and_check()
