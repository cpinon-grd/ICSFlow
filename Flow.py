import argparse
from scapy.layers.l2 import Ether
from scapy.all import *
from Helper import setup_logger, get_packet_time, format_time, format_decimal, average, maximum
from PacketParameter import PacketParameter


class Flow:
    HEADER_PRINTED = False
    REFERENCE_TIME = 0
    Attacks = False

    @classmethod
    def compile(cls):
        args = Flow.get_args()
        Flow.Attacks = args.attacks

        # create loggers
        dataset = setup_logger(
            args.output, logging.Formatter('%(message)s'), file_dir="./", file_ext='.csv')
        logger = setup_logger(
            args.output + "_log", logging.Formatter('%(message)s'), file_dir="./", file_ext='.txt')

        count = 0
        Flow.HEADER_PRINTED = False
        flow_dict = dict()

        for (pkt_data, pkt_metadata,) in RawPcapReader(args.input):
            count += 1
            pkt_time = get_packet_time(pkt_metadata)

            if count == 1:
                Flow.REFERENCE_TIME = pkt_time

            ether_pkt = Ether(pkt_data)

            if 'type' not in ether_pkt.fields:
                logger.info(
                    "Note: LLC frames Packet:{} on {}({})".format(count, format_time(pkt_time), pkt_time))
                continue

            packet_para = PacketParameter(ether_pkt, pkt_time, logger)
            flow_src = min(packet_para.get_src(), packet_para.get_dst())
            flow_dst = max(packet_para.get_src(), packet_para.get_dst())
            flow_proto = packet_para.protocol_name

            if not flow_dict.keys().__contains__((flow_src, flow_dst, flow_proto)):
                flow_dict[(flow_src, flow_dst, flow_proto)] = Flow(flow_src,
                                                                   flow_dst,
                                                                   flow_proto,
                                                                   args.interval,
                                                                   dataset)

            flow_dict[(flow_src, flow_dst, flow_proto)].add_packet(packet_para)

            if count % 1000 == 0:
                print(count)

        for flow in flow_dict.values():
            flow.flush_flow()

    @classmethod
    def get_args(cls):
        parser = argparse.ArgumentParser(description='PCAP reader')
        parser.add_argument('--input', metavar='<pcap file name>',
                            help='pcap file to parse', required=True)
        parser.add_argument('--output', metavar='<csv file name>',
                            help='csv file to ouput', required=True)
        parser.add_argument('--interval', metavar='interval in seconds', type=float, default=0.5,
                            help='interval to compute flows', required=False)

        parser.add_argument('--attacks', metavar='attack log csv file',
                            help='attack file to classify flows', required=False)

        parser.parse_args()
        args = parser.parse_args()

        if not os.path.isfile(args.input):
            print('"{}" does not exist'.format(args.input), file=sys.stderr)
            sys.exit(-1)

        if args.attacks:
            if not os.path.isfile(str(args.attacks)):
                print('"{}" does not exist'.format(args.attacks), file=sys.stderr)
                sys.exit(-1)

            attacks = []
            with open(str(args.attacks)) as f:
                lines = f.readlines()

            lines.pop(0)
            for line in lines:
                if line.isspace():
                    continue
                paras = line.strip().split(',')
                attacks.append([paras[0],
                                datetime.fromisoformat(paras[3].strip()).timestamp(),
                                datetime.fromisoformat(paras[4].strip()).timestamp(),
                                paras[5],
                                paras[6]]
                               )
            args.attacks = attacks
        else:
            args.attacks = False
        return args

    @classmethod
    def iterate_packets(cls):
        count = 0
        args = Flow.get_args()

        for (pkt_data, pkt_metadata,) in RawPcapReader(args.input):
            count += 1

            if count % 10000 == 0:
                print(count)
        print(count)

    def __init__(self, src, dst, protocol, interval, dataset):
        self.src = min(src, dst)
        self.des = max(src, dst)
        self.protocol = protocol
        self.interval = interval
        self.dataset = dataset

        self.sen_list = []
        self.rec_list = []
        self.acc_sen_dic = dict()
        self.acc_rec_dic = dict()
        self.sen_delay = []
        self.rec_delay = []
        self.src_ip_list = set()
        self.dst_ip_list = set()
        self.src_mac_list = set()
        self.dst_mac_list = set()

    def reset(self):
        self.__init__(self.src, self.des, self.protocol, self.interval, self.dataset)

    def start_time(self):
        s_start = sys.float_info.max
        r_start = sys.float_info.max
        if len(self.sen_list) != 0:
            s_start = self.sen_list[0].packet_time
        if len(self.rec_list) != 0:
            r_start = self.rec_list[0].packet_time

        return min(s_start, r_start)

    def end_time(self):
        s_end = 0
        r_end = 0
        if len(self.sen_list) != 0:
            s_end = self.sen_list[-1].packet_time
        if len(self.rec_list) != 0:
            r_end = self.rec_list[-1].packet_time

        return max(s_end, r_end)

    def get_window(self):
        return self.end_time() - self.start_time() + 0.000001

    def is_empty(self):
        return len(self.rec_list) == 0 and len(self.sen_list) == 0

    def add_packet(self, packet_parameter):
        if not self.can_append(packet_parameter.packet_time):
            self.flush_flow()

        if packet_parameter.get_src() == self.src:
            self.sen_list.append(packet_parameter)
        else:
            self.rec_list.append(packet_parameter)

        self.compute_delay(packet_parameter)

        if packet_parameter.is_ip():
            if packet_parameter.get_src() == self.src:
                self.src_ip_list.add(packet_parameter.src_ip)
                self.dst_ip_list.add(packet_parameter.dst_ip)
                self.src_mac_list.add(packet_parameter.src_mac)
                self.dst_mac_list.add(packet_parameter.dst_mac)
            else:
                self.src_ip_list.add(packet_parameter.dst_ip)
                self.dst_ip_list.add(packet_parameter.src_ip)
                self.src_mac_list.add(packet_parameter.dst_mac)
                self.dst_mac_list.add(packet_parameter.src_mac)

    def can_append(self, packet_time):
        if self.is_empty():
            return True

        return self.start_time() + self.interval > packet_time

    def compute_delay(self, packet_parameter):
        if not packet_parameter.is_tcp():
            return

        if packet_parameter.get_src() == self.src:
            self.acc_sen_dic[packet_parameter.ack] = packet_parameter.packet_time
            if self.acc_rec_dic.keys().__contains__(packet_parameter.seq):
                self.sen_delay.append(packet_parameter.packet_time - self.acc_rec_dic[packet_parameter.seq])
        else:
            self.acc_rec_dic[packet_parameter.ack] = packet_parameter.packet_time
            if self.acc_sen_dic.keys().__contains__(packet_parameter.seq):
                self.rec_delay.append(packet_parameter.packet_time - self.acc_sen_dic[packet_parameter.seq])

    def flush_flow(self):
        result = self.compute_parameters()
        if not Flow.HEADER_PRINTED:
            Flow.HEADER_PRINTED = True
            self.dataset.info(','.join(result.keys()))
        self.dataset.info(','.join(result.values()))
        self.reset()

    def compute_parameters(self):
        res = dict()

        # flow features
        res["sAddress"] = self.src
        res["rAddress"] = self.des
        res["sMACs"] = '/'.join(self.src_mac_list)
        res["rMACs"] = '/'.join(self.dst_mac_list)
        res["sIPs"] = '/'.join(self.src_ip_list)
        res["rIPs"] = '/'.join(self.dst_ip_list)
        res["Protocol"] = str(self.protocol)

        # General features part 1
        res["startDate"] = str(format_time(self.start_time()))
        res["endDate"] = str(format_time(self.end_time()))
        res["start"] = str(format_decimal(self.start_time(), 6))
        res["end"] = str(format_decimal(self.end_time(), 6))
        res["startOffset"] = str(format_decimal(self.start_time() - Flow.REFERENCE_TIME, 6))
        res["endOffset"] = str(format_decimal(self.end_time() - Flow.REFERENCE_TIME, 6))

        self.compute_dual_parameters('s', self.sen_list, res)
        self.compute_dual_parameters('r', self.rec_list, res)

        # TCP features part 2
        res["sAckDelay"] = str(average(self.sen_delay))
        res["rAckDelay"] = str(average(self.rec_delay))
        res["sMaxAckDelay"] = str(maximum(self.sen_delay))
        res["rMaxAckDelay"] = str(maximum(self.rec_delay))

        it_b_label = '0'
        it_m_label = 'Normal'
        nst_b_label = '0'
        nst_m_label = 'Normal'

        if Flow.Attacks:
            for i in range(len(Flow.Attacks)):
                if not (Flow.Attacks[i][1] >= self.end_time() or Flow.Attacks[i][2] <= self.start_time()):
                    it_b_label = 1
                    it_m_label = Flow.Attacks[i][0]
                    attacker_mac = Flow.Attacks[i][3]
                    attacker_ip = Flow.Attacks[i][4]
                    if attacker_mac in self.src_mac_list or \
                            attacker_mac in self.dst_mac_list or \
                            attacker_ip in self.src_mac_list or \
                            attacker_ip in self.dst_mac_list:
                        nst_b_label = 1
                        nst_m_label = Flow.Attacks[i][0]

            res["IT-B-Label"] = it_b_label
            res["IT-M-Label"] = it_m_label
            res["NST-B-Label"] = nst_b_label
            res["NST-M-Label"] = nst_m_label

        return res

    def compute_dual_parameters(self, prefix, target, res):
        # General features part 2
        res[prefix + "Packets"] = str(Flow.packets_cnt(target))
        res[prefix + "Bytes"] = str(Flow.packets_bytes_sum(target))
        res[prefix + "BytesAvg"] = str(Flow.packets_bytes_avg(target))
        res[prefix + "Load"] = str(self.load(target))
        res[prefix + "Payload"] = str(Flow.payload_sum(target))
        res[prefix + "PayloadAvg"] = str(Flow.payload_avg(target))
        res[prefix + "InterPacket"] = str(Flow.inter_packets_avg(target))

        # TCP features Part 1
        res[prefix + "ttl"] = str(Flow.ttl_avg(target))
        res[prefix + "AckRate"] = str(Flow.flag_rate(target, 'A'))
        res[prefix + "FinRate"] = str(Flow.flag_rate(target, 'F'))
        res[prefix + "PshRate"] = str(Flow.flag_rate(target, 'P'))
        res[prefix + "SynRate"] = str(Flow.flag_rate(target, 'S'))
        res[prefix + "UrgRate"] = str(Flow.flag_rate(target, 'U'))
        res[prefix + "RstRate"] = str(Flow.flag_rate(target, 'R'))
        res[prefix + "WinTCP"] = str(Flow.tcp_window_avg(target))
        res[prefix + "FragmentRate"] = str(Flow.fragmentation_rate(target))

    @staticmethod
    def packets_cnt(packets):
        return len(packets)

    @staticmethod
    def packets_bytes_sum(packets):
        return sum([pkt.length for pkt in packets])

    @staticmethod
    def packets_bytes_avg(packets):
        if Flow.packets_cnt(packets) == 0:
            return ''
        else:
            value = Flow.packets_bytes_sum(packets) / Flow.packets_cnt(packets)
            return format_decimal(format_decimal(value))

    @staticmethod
    def payload_sum(packets):
        return sum([pkt.payload for pkt in packets])

    @staticmethod
    def payload_avg(packets):
        if Flow.packets_cnt(packets) == 0:
            return ''
        else:
            return format_decimal(sum([pkt.payload for pkt in packets]) / Flow.packets_cnt(packets))

    @staticmethod
    def inter_packets_avg(packets):
        if Flow.packets_cnt(packets) == 0:
            return ''

        if Flow.packets_cnt(packets) == 1:
            return ''

        return (packets[-1].packet_time - packets[0].packet_time) / (Flow.packets_cnt(packets) - 1)

    @staticmethod
    def ttl_avg(packets):

        if Flow.packets_cnt(packets) == 0:
            return ''
        if not packets[0].is_tcp():
            return ''

        if not packets[0].is_ip():
            return ''
        else:
            value = sum([pkt.ttl for pkt in packets]) / Flow.packets_cnt(packets)
            return format_decimal(value)

    @staticmethod
    def flag_rate(packets, flag):
        if Flow.packets_cnt(packets) == 0:
            return ''
        if not packets[0].is_tcp():
            return ''

        value = 0

        match flag:
            case 'A':
                value = sum([int(pkt.flags.A) for pkt in packets]) / Flow.packets_cnt(packets)
            case 'U':
                value = sum([int(pkt.flags.U) for pkt in packets]) / Flow.packets_cnt(packets)
            case 'S':
                value = sum([int(pkt.flags.S) for pkt in packets]) / Flow.packets_cnt(packets)
            case 'F':
                value = sum([int(pkt.flags.F) for pkt in packets]) / Flow.packets_cnt(packets)
            case 'R':
                value = sum([int(pkt.flags.R) for pkt in packets]) / Flow.packets_cnt(packets)
            case 'P':
                value = sum([int(pkt.flags.P) for pkt in packets]) / Flow.packets_cnt(packets)
            case _:
                raise Exception('Should not end here.')

        return format_decimal(value)

    @staticmethod
    def tcp_window_avg(packets):
        if Flow.packets_cnt(packets) == 0:
            return ''
        if not packets[0].is_tcp():
            return ''

        return format_decimal(sum([pkt.window for pkt in packets]) / Flow.packets_cnt(packets))

    @staticmethod
    def fragmentation_rate(packets):

        if Flow.packets_cnt(packets) == 0:
            return ''
        if not packets[0].is_ip():
            return ''

        return sum([int(pkt.fragment) for pkt in packets]) / Flow.packets_cnt(packets)

    def load(self, packets):
        value = Flow.packets_bytes_sum(packets) * 8 / self.get_window()
        return format_decimal(value)


if __name__ == '__main__':
    Flow.compile()
