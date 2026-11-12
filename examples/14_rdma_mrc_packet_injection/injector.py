import socket, struct, sys, time, random, json

FPGA_IP = "192.168.1.2" 			# c0a80102, change it according to your setup
PEER_QPN = 0x100         			# qp->remote.qpn set in the -p block, the FPGA sends its replies to this QPN 
									# only useful to check that Coyote sent to the correct QPN
OUTPUT_NET_DEV = b"enxd0c0bf267d20"	# change it according to your setup
NUM_OF_PACKETS = 5					# for now we only send write only 0xca
STATE_FILE = "fpga_state.json"
TIMEOUT = None 						# a number or None, use None for replays of the integration test
IDLE = 0.2							# seconds of silence that end a group (fast mode)
MEM_SIZE = 1 << 20					# must match -X of the C++ program
WITH_IMM = False					# True: random mode sends WRITE ONLY with Immediate (0xcb) instead of WRITE ONLY (0xca)

# receive parameters from argv
qpn, psn, vaddr, msn = (int(x, 0) for x in sys.argv[1:5])
trace_file, tb_qp = (sys.argv[5], int(sys.argv[6], 0)) if len(sys.argv) > 6 else (None, None)

cack, msn_now, arrived = (psn - 1) & 0xFFFFFF, msn, set()
if not trace_file:
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

def create_meth(msn, rqmsn=0):
	meth = struct.pack(">HH",
		rqmsn & 0xFFFF,			# rqmsn (only meaningful for WriteIMM)
		msn & 0xFFFF)			# requestor msn
	return meth

def create_reth(vaddr):
	reth = struct.pack(">QII", 
		vaddr, 					# vaddr of THIS packet (MRC: every packet carries a RETH)
		0, 						# rkey
		64)						# dma length
	return reth
	
def create_rdma_write_packet(psn, msn, addr, data, rqmsn=None, imm=None):
	if imm is None:
		return create_bth(0xCA, psn) + create_meth(msn) + create_reth(addr) + data + bytes(4)
	return create_bth(0xCB, psn) + create_meth(msn, rqmsn) + create_reth(addr) + struct.pack(">I", imm) + data + bytes(4)

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
		while k < len(sending_packets) or time.monotonic() - last < IDLE:
			if k < len(sending_packets) and (k == 0 or not group_end[k - 1] or time.monotonic() - last >= IDLE):
				if k == 0 or group_end[k - 1]:
					real_received_packet.append([])
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
			real_received_packet[-1].append(reply)
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
	failures = 0
	acks = [[r for r in replies if r[0] == 0xD1] for replies in real_received_packet]
	for i, expected in enumerate(expected_received_packet):
		j = expected[5]
		g = j if TIMEOUT is not None else sum(group_end[:j])
		print("Checking ACK", i, "triggered by psn", hex(int.from_bytes(sending_packets[j][9:12], "big")), "(" + packet_kind[j] + ")")
		if not acks[g]:
			print("  BAD no ACK")
			failures += 1
			continue
		reply = acks[g].pop(0)
		opcode, _, _, r_qpn, r_psn = struct.unpack(">BBHII", reply[:12])
		aeth, = struct.unpack(">I", reply[12:16])
		received = (opcode, r_qpn & 0xFFFFFF, r_psn & 0xFFFFFF, aeth >> 24, aeth & 0xFFFFFF)
		for name, e, r in zip(("opcode", "qpn", "psn", "syndrome", "msn"), expected, received):
			print("  OK " if e == r else "  BAD", name, "expected", hex(e), "received", hex(r))
			failures += e != r
	for g, left in enumerate(acks):
		for r in left:
			nak = (r[12] >> 5) & 0x3 == 0x3
			print("  NAK" if nak else "  BAD", "leftover psn", hex(int.from_bytes(r[9:12], "big")), "msn", int.from_bytes(r[13:16], "big"),
				"after", packet_kind[g] if TIMEOUT is not None else "group %d" % g)
			failures += not nak
	for replies in real_received_packet:
		for r in replies:
			if r[0] == 0xDC:
				print("SACK cack_psn", hex(int.from_bytes(r[25:28], "big")), "bitmap", r[32:40].hex())
			elif r[0] != 0xD1:
				print("  BAD unexpected opcode", hex(r[0]))
				failures += 1
	if trace_file:
		with open("expected_mem.txt", "w") as f:
			for a in range(0, MEM_SIZE, 32):
				if any(expected_mem[a:a + 32]):
					f.write("%5u: %s\n" % (a, " ".join("%02x" % b for b in expected_mem[a:a + 32])))
	return failures

def save_state():
	if not trace_file:
		acks = [r for replies in real_received_packet for r in replies if r[0] == 0xD1]	
		if acks and (int.from_bytes(acks[-1][9:12], "big"), int.from_bytes(acks[-1][13:16], "big")) != (cack, msn_now):
			print("WARNING: the last ACK does not match the model, packets were probably lost. Restart the C++ program and delete", STATE_FILE)
		json.dump({"qpn": qpn, "vaddr": vaddr, "cack": cack, "msn": msn_now, "arrived": sorted(arrived)}, open(STATE_FILE, "w"))

print("+++ MRC Coyote Test +++\n")

print("--- Populating sending_packets and expected_received_packet ---\n")
packet_kind = []
group_end = []
expected_mem = bytearray(MEM_SIZE)
if trace_file:
	completions = 0
	for line in open(trace_file):
		if not line.strip():
			continue
		row = json.loads(line)
		if "qp" in row:
			if row["qp"] == tb_qp and (row["epsn"], 0) != (psn, msn):
				print("WARNING: the testbench starts QP", hex(tb_qp), "at psn", hex(row["epsn"]), "msn 0: restart the C++ program with -s", hex(row["epsn"]))
			continue
		p = bytearray.fromhex(row["packet"])
		dqp = int.from_bytes(p[5:8], "big")
		if dqp != tb_qp and dqp != 0x2:
			if row["group_end"] and group_end:
				group_end[-1] = True
			continue
		if dqp == tb_qp:
			p[5:8] = qpn.to_bytes(3, "big")
		if 0xC6 <= p[0] <= 0xCB:
			o = 20 if p[8] & 0x10 else 16
			va = int.from_bytes(p[o:o + 8], "big")
			p[o:o + 8] = (vaddr + va).to_bytes(8, "big")
			if row["exp_accepted"]:
				data = p[o + 16 + (4 if p[0] in (0xC9, 0xCB) else 0):]
				expected_mem[va:va + len(data)] = data
			if row["exp_accepted"] and p[0] in (0xC8, 0xC9, 0xCA, 0xCB):
				completions += 1
		if row["exp_ack"]:
			expected_received_packet.append((0xD1, PEER_QPN, row["exp_cack_psn"], row["exp_syndrome"], row["exp_ack_msn"], len(sending_packets)))
		sending_packets.append(bytes(p) + bytes(4))
		packet_kind.append(row["name"])
		group_end.append(row["group_end"])
		print("Expected WRITE messages completed on the C++ side:", completions)
else:
	run_id = random.randint(1, 255)
	print("The run id for this \"burst\" of packets is ", hex(run_id))
	# WriteIMM: one per message, so RQMSN = completed messages + 1 (true only if this QP has received only WriteIMMs)
	expected_imm = []
	for i in range(NUM_OF_PACKETS):
		p = (psn + i) & 0xFFFFFF
		if WITH_IMM:
			expected_imm.append((run_id << 16) | (i + 1))
			sending_packets.append(create_rdma_write_packet(p, msn + i, vaddr + i * 64, bytes([i + 1, run_id]) * 32, msn_now + i + 1, expected_imm[-1]))
		else:
			sending_packets.append(create_rdma_write_packet(p, msn + i, vaddr + i * 64, bytes([i + 1, run_id]) * 32))
	if WITH_IMM:
		print("ImmDt the C++ program must print, in this order:", [hex(x) for x in expected_imm])
	random.shuffle(sending_packets)
	group_end = [False] * len(sending_packets)
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
		expected_received_packet.append((0xD1, PEER_QPN, cack, 0x1f, msn_now & 0xFFFFFF, j))
print("Send order (psn):", [hex(int.from_bytes(p[9:12], "big")) for p in sending_packets])
print("")

print("--- Creating UDP Socket ---\n")
my_socket = create_udp_socket()
print("--- Sending Packets ---\n")
send_packets(my_socket)
print("--- Checking ---\n")
failures = print_reply_and_check()
save_state()
print("\nRESULT:", "PASS" if failures == 0 else "FAIL, %d check(s) failed" % failures)
sys.exit(1 if failures else 0)
