import socket, struct, sys, time, random, json

FPGA_IP = "192.168.1.2" 			# c0a80102, change it according to your setup
PEER_QPN = 0x100         			# qp->remote.qpn set in the -p block, the FPGA sends its replies to this QPN 
									# only useful to check that Coyote sent to the correct QPN
OUTPUT_NET_DEV = b"enxd0c0bf267d20"	# change it according to your setup
NUM_OF_PACKETS = 5					# for now we only send write only 0xca
STATE_FILE = "fpga_state.json"
TIMEOUT = 2 						# a number or None

# receive parameters from argv
qpn, psn, vaddr, msn = (int(x, 0) for x in sys.argv[1:5])

cack, msn_now, arrived = (psn - 1) & 0xFFFFFF, msn, set()
try:
	state = json.load(open(STATE_FILE))
	if state["qpn"] == qpn and state["vaddr"] == vaddr:
		cack, msn_now, arrived = state["cack"], state["msn"], set(state["arrived"])
		print("Using saved FPGA state: cack", hex(cack), "msn", msn_now, "received above cack", [hex(x) for x in sorted(arrived)])
except FileNotFoundError:
	pass

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
	if TIMEOUT is not None:
		s.settimeout(TIMEOUT)
	else:
		s.setblocking(False)
	return s
	
def wait(seconds=3):
    for i in range(1, seconds + 1):
        print(i, "...")
        time.sleep(1)

def send_packets(my_socket):
	pending = []
	t0 = time.monotonic()
	if TIMEOUT is not None:
		for k, packet in enumerate(sending_packets):
			p = int.from_bytes(packet[9:12], "big")
			my_socket.sendto(packet, (FPGA_IP, 4791))
			pending.append(p)
			print("%9.3f ms  sent psn %s (%s)" % ((time.monotonic() - t0) * 1000, hex(p), packet_kind[k]))
			real_received_packet.append([])
			try:
				while True:
					reply, src = my_socket.recvfrom(65535)
					real_received_packet[-1].append(reply)
					if reply[0] == 0xD1:
						cack = int.from_bytes(reply[9:12], "big")
						pending = [x for x in pending if ((cack - x) & 0xFFFFFF) >= 0x800000]
						print("%9.3f ms  ACK  psn %s msn %d, still waiting for %d: %s" % ((time.monotonic() - t0) * 1000,
							hex(cack), int.from_bytes(reply[13:16], "big"), len(pending), [hex(x) for x in pending]))
			except TimeoutError:
				pass
	else:
		log = []
		k = 0
		last = t0
		real_received_packet.append([])
		while (k < len(sending_packets) or sum(r[0] == 0xD1 for r in real_received_packet[0]) < len(expected_received_packet)) and time.monotonic() - last < 1:
			if k < len(sending_packets):
				p = int.from_bytes(sending_packets[k][9:12], "big")
				my_socket.sendto(sending_packets[k], (FPGA_IP, 4791))
				last = time.monotonic()
				pending.append(p)
				log.append("%9.3f ms  sent psn %s (%s)" % ((last - t0) * 1000, hex(p), packet_kind[k]))
				k += 1
			try:
				reply, src = my_socket.recvfrom(65535)
			except BlockingIOError:
				continue
			last = time.monotonic()
			real_received_packet[0].append(reply)
			if reply[0] == 0xD1:
				cack = int.from_bytes(reply[9:12], "big")
				pending = [x for x in pending if ((cack - x) & 0xFFFFFF) >= 0x800000]
				log.append("%9.3f ms  ACK  psn %s msn %d, still waiting for %d: %s" % ((last - t0) * 1000,
					hex(cack), int.from_bytes(reply[13:16], "big"), len(pending), [hex(x) for x in pending]))
		print("\n".join(log))
	print("Not acknowledged at the end:", [hex(x) for x in pending] or "none")
	acks = [r for replies in real_received_packet for r in replies if r[0] == 0xD1]
	if acks:
		print("FPGA state after the burst: expected psn", hex((int.from_bytes(acks[-1][9:12], "big") + 1) & 0xFFFFFF),
			"msn", int.from_bytes(acks[-1][13:16], "big"))
	print("")

def print_reply_and_check():
	if TIMEOUT is None:
		all_acks = [r for r in real_received_packet[0] if r[0] == 0xD1]
		if len(all_acks) != len(expected_received_packet):
			print("BAD expected", len(expected_received_packet), "ACKs, received", len(all_acks))
	for i, expected in enumerate(expected_received_packet):
		j = expected[5]
		print("Checking ACK", i, "triggered by psn", hex(int.from_bytes(sending_packets[j][9:12], "big")), "(" + packet_kind[j] + ")")
		if TIMEOUT is not None:
			acks = [r for r in real_received_packet[j] if r[0] == 0xD1]
		else:
			acks = all_acks[i:i + 1]
		if len(acks) != 1:
			print("  BAD expected 1 ACK, received", len(acks))
		if acks:
			opcode, _, _, r_qpn, r_psn = struct.unpack(">BBHII", acks[0][:12])
			aeth, = struct.unpack(">I", acks[0][12:16])
			received = (opcode, r_qpn & 0xFFFFFF, r_psn & 0xFFFFFF, aeth >> 29, aeth & 0xFFFFFF)
			for name, e, r in zip(("opcode", "qpn", "psn", "ack type", "msn"), expected, received):
				print("  OK " if e == r else "  BAD", name, "expected", hex(e), "received", hex(r))
			print("  syndrome", hex(aeth >> 24))
	for replies in real_received_packet:
		for r in replies:
			if r[0] == 0xDC:
				cack = int.from_bytes(r[25:28], "big")
				print("SACK cack_psn", hex(cack),
					"ack_psn", hex((cack + int.from_bytes(r[14:16], "big", signed=True)) & 0xFFFFFF),
					"sack_base", hex((cack + int.from_bytes(r[30:32], "big", signed=True)) & 0xFFFFFF),
					"bitmap", r[32:40].hex(),
					"ooo_count", int.from_bytes(r[42:44], "big") & 0x7FFF)
			elif r[0] != 0xD1:
				print("Unexpected opcode", hex(r[0]))

def save_state():
	acks = [r for replies in real_received_packet for r in replies if r[0] == 0xD1]	
	if acks and (int.from_bytes(acks[-1][9:12], "big"), int.from_bytes(acks[-1][13:16], "big")) != (cack, msn_now):
		print("WARNING: the last ACK does not match the model, packets were probably lost. Restart the C++ program and delete", STATE_FILE)
	json.dump({"qpn": qpn, "vaddr": vaddr, "cack": cack, "msn": msn_now, "arrived": sorted(arrived)}, open(STATE_FILE, "w"))

print("+++ MRC Coyote Test +++\n")

print("--- Populating sending_packets and expected_received_packet ---\n")
run_id = random.randint(1, 255)
print("The run id for this \"burst\" of packets is ", hex(run_id))
for i in range(NUM_OF_PACKETS):
	print("Building packet ", i)
	p = (psn + i) & 0xFFFFFF
	sending_packets.append(create_rdma_write_packet(p, msn + i, vaddr + i * 64, bytes([i + 1, run_id]) * 32))
random.shuffle(sending_packets) # make the packets out of order

packet_kind = []
for j, packet in enumerate(sending_packets):
	p = int.from_bytes(packet[9:12], "big")
	d = (p - cack - 1) & 0xFFFFFF
	if 128 <= d <= 0x800000:
		packet_kind.append("out of window, no ACK")
		continue
	if d >= 128:
		packet_kind.append("duplicate, behind cack")
	elif p in arrived:
		packet_kind.append("duplicate, already received above cack")
	else:
		packet_kind.append("new" if d == 0 else "new, ahead of a hole")
		arrived.add(p)
	while ((cack + 1) & 0xFFFFFF) in arrived:
		cack = (cack + 1) & 0xFFFFFF
		arrived.remove(cack)
		msn_now += 1
	expected_received_packet.append((0xD1, PEER_QPN, cack, 0, msn_now & 0xFFFFFF, j))
print("Send order (psn):", [hex(int.from_bytes(p[9:12], "big")) for p in sending_packets])
print("")

print("--- Creating UDP Socket ---\n")
my_socket = create_udp_socket()
print("--- Sending Packets ---\n")
send_packets(my_socket)
print("--- Checking ---\n")
print_reply_and_check()
save_state()
