import socket, struct, sys, time, random

FPGA_IP = "192.168.1.2" 			# c0a80102, change it according to your setup
PEER_QPN = 0x100         			# qp->remote.qpn set in the -p block, the FPGA sends its replies to this QPN 
									# only useful to check that Coyote sent to the correct QPN
OUTPUT_NET_DEV = b"enxd0c0bf267d20"	# change it according to your setup
NUM_OF_PACKETS = 3					# for now we only send write only 0xca

# receive parameters from argv
qpn, psn, vaddr, msn = (int(x, 0) for x in sys.argv[1:5])

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
		opcode, 				# opcode
		0, 						# flag byte
		0xFFFF, 				# partition key
		qpn, 					# destination qpn
		(1 << 31) | psn)		# a (ack request) + R=0, rtx=0, ts=0 + psn
	return bth

def create_meth(msn):
	meth = struct.pack(">HH",
		0,						# rqmsn (only meaningful for WriteIMM)
		msn & 0xFFFF)			# requestor msn
	return meth

def create_reth(vaddr):
	reth = struct.pack(">QII", 
		vaddr, 					# vaddr of THIS packet (MRC: every packet carries a RETH)
		0, 						# rkey
		64)						# dma length
	return reth
	
def create_rdma_write_packet(psn, msn, addr, data):
	packet = create_bth(0xCA, psn) + create_meth(msn) + create_reth(addr) + data + bytes(4)
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
	random.shuffle(sending_packets) # make the packets out of order
	for packet in sending_packets:
		my_socket.sendto(packet, (FPGA_IP, 4791))
		try:
			while True:
				reply, src = my_socket.recvfrom(65535)
				real_received_packet.append(reply)
		except TimeoutError:
			pass
	
def print_reply_and_check():
	acks = {struct.unpack(">I", r[8:12])[0] & 0xFFFFFF: r for r in real_received_packet if r[0] == 0xD1}
	for i, expected in enumerate(expected_received_packet):
		print("Checking", i)
		reply = acks.pop(expected[2], b"")
		if not reply:
			print("  BAD no ACK")
			continue
		opcode, _, _, r_qpn, r_psn = struct.unpack(">BBHII", reply[:12])
		aeth, = struct.unpack(">I", reply[12:16])
		received = (opcode, r_qpn & 0xFFFFFF, r_psn & 0xFFFFFF, aeth >> 29, aeth & 0xFFFFFF)
		for name, e, r in zip(("opcode", "qpn", "psn", "ack type", "msn"), expected, received):
			print("  OK " if e == r else "  BAD", name, "expected", hex(e), "received", hex(r))
		print("  syndrome", hex(aeth >> 24))
	for psn, reply in acks.items():
		print("Unexpected ACK psn", hex(psn), "syndrome", hex(reply[12]))
	for r in real_received_packet:
		if r[0] == 0xDC:
			cack = int.from_bytes(r[25:28], "big")
			print("SACK cack_psn", hex(cack),
				"ack_psn", hex((cack + int.from_bytes(r[14:16], "big", signed=True)) & 0xFFFFFF),
				"sack_base", hex((cack + int.from_bytes(r[30:32], "big", signed=True)) & 0xFFFFFF),
				"bitmap", r[32:40].hex(),
				"ooo_count", int.from_bytes(r[42:44], "big") & 0x7FFF)
		elif r[0] != 0xD1:
			print("Unexpected opcode", hex(r[0]))
			
print("+++ MRC Coyote Test +++\n")

print("--- Populating sending_packets and expected_received_packet ---\n")
run_id = random.randint(1, 255)
print("The run id for this \"burst\" of packets is ", hex(run_id))
for i in range(NUM_OF_PACKETS):
	print("Building packet ", i)
	p = (psn + i) & 0xFFFFFF
	sending_packets.append(create_rdma_write_packet(p, msn + i, vaddr + i * 64, bytes([i + 1, run_id]) * 32))
	expected_received_packet.append((0xD1, PEER_QPN, p, 0, (msn + i + 1) & 0xFFFFFF))
print("")

print("--- Creating UDP Socket ---\n")
my_socket = create_udp_socket()
print("--- Sending Packets ---\n")
send_packets(my_socket)
print("--- Checking ---\n")
print_reply_and_check()