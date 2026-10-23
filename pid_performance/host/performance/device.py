"""Existing 115200 UART protocol, with fixed-clock buffered acquisition."""
import time
import numpy as np
import serial


class Device:
    def __init__(self, port):
        self.s=serial.Serial(port=None, baudrate=115200, timeout=.8, write_timeout=2)
        self.s.dtr=False; self.s.rts=False; self.s.port=port
        self.s.open()
        self.original_probe=None
        try:
            if self.read(0x3f)!=0xA623:
                raise RuntimeError('板上不是 A623 测量固件；未改变任何控制参数')
            self.original_probe=self.read(0x30)
            self.original_stream=self.read(0x28)
            if self.original_probe:
                raise RuntimeError('已有测量/扰动正在运行，请先停止原程序')
            self.write(0x28,0)
            self.snapshot={hex(a):self.read(a) for a in
                [0,1,0x11,0x18,0x19,0x1a,0x1b,0x1c,0x1d,0x1e,0x1f,0x20,0x21,0x2d,0x2e,0x2f,0x38,0x3f]}
            self.raw_fs=50e6/(2*(self.snapshot['0x11']+1))
        except Exception:
            if self.original_probe==0 and hasattr(self,'original_stream'):
                self.write(0x28,self.original_stream)
            self.s.close(); raise

    @staticmethod
    def frame(cmd,addr,value=0):
        return bytes([197,cmd,addr,value&255,(value>>8)&255,92,197,197])

    def write(self,addr,value):
        self.s.write(self.frame(1,addr,value))

    def read(self,addr):
        self.s.write(self.frame(2,addr))
        deadline=time.monotonic()+2
        packet=bytearray()
        while time.monotonic()<deadline:
            packet.extend(self.s.read(max(1,8-len(packet))))
            while len(packet)>=8:
                if packet[0]==197 and packet[5:8]==bytes([92,197,197]):
                    b=bytes(packet[:8]);del packet[:8]
                    if b[1]==2 and b[2]==addr:
                        return b[3]+256*b[4]
                else:del packet[0]
        raise TimeoutError(f'FPGA 寄存器 0x{addr:02X} 无有效应答')

    def require_closed_loop(self):
        ctrl=self.read(0)
        if ctrl&15!=1 or self.read(0x2e)!=1:
            raise RuntimeError('请先用原 PID 程序启用并稳定闭环；本界面不猜测 PID 参数')
        if self.read(0x23)&1:
            raise RuntimeError('请关闭原有目标扰动，再测自然噪声或输出扰动')
        if self.read(1)&0x35:
            raise RuntimeError('闭环存在丢光、ADC溢出、输出限幅或冻结，先恢复正常工作点')

    def hold(self,on):
        value=(self.read(0x30)&1)|(2 if on else 0)
        self.write(0x30,value|4)
        if self.read(0x30)!=value:raise RuntimeError('反馈保持状态未读回')

    def clear_light_loss(self):
        ctrl=self.read(0)
        self.write(0,ctrl|512);self.write(0,ctrl&~512)

    def inject(self,frequency,amplitude):
        ctl=self.read(0x30)&2
        self.write(0x30,ctl|4)
        if not 0<=amplitude<=16383:raise ValueError('扰动幅度超出 DAC 范围')
        if frequency==0:
            self.write(0x31,0);return 0.0
        if not 0<frequency<=self.raw_fs/20:raise ValueError('扰动频率超过原始采样率的 1/20')
        inc=round(frequency/self.raw_fs*2**32)
        self.write(0x31,amplitude);self.write(0x32,inc&65535);self.write(0x33,inc>>16)
        self.write(0x30,ctl|1|4)
        if self.read(0x32)!=inc&65535 or self.read(0x33)!=inc>>16:
            raise RuntimeError('正弦参数读回不一致')
        return inc*self.raw_fs/2**32

    def capture(self,decim_log,cancel,progress=lambda text:None):
        if not 0<=decim_log<=14:raise ValueError('抽取指数必须为 0–14')
        self.write(0x35,decim_log)
        if self.read(0x35)!=decim_log:raise RuntimeError('采集抽取设置未生效')
        before=self.read(0x3d);self.write(0x34,1)
        fs=self.raw_fs/2**decim_log
        started=time.monotonic();last_progress=-1
        deadline=time.monotonic()+8198/fs+5
        while True:
            if cancel.is_set():self.write(0x34,2);raise InterruptedError('测量已停止')
            state=self.read(0x36)
            if state&2:break
            elapsed=int(time.monotonic()-started)
            if elapsed!=last_progress:
                last_progress=elapsed
                progress(f'采集中：已过 {elapsed} 秒，预计还需 {max(0,8198/fs-elapsed):.0f} 秒')
            if time.monotonic()>deadline:raise TimeoutError('FPGA 定时缓存采集未完成')
            cancel.wait(.05)
        seq=self.read(0x3d)
        if seq!=(before+1)&65535 or self.read(0x3e)!=8192:
            raise RuntimeError('缓存序号或长度异常，拒绝使用旧数据')
        raw=np.empty(8192,dtype=np.uint16)
        # Batch requests to avoid a Windows USB latency timer on every sample.
        # Each read is preceded by an index write; FPGA responses remain ordered.
        for i in range(0,len(raw),64):
            if cancel.is_set():raise InterruptedError('测量已停止')
            self.s.write(b''.join(self.frame(1,0x37,j)+self.frame(2,0x3b)
                                 for j in range(i,i+64)))
            reply=bytearray();deadline=time.monotonic()+3
            while len(reply)<64*8:
                reply.extend(self.s.read(64*8-len(reply)))
                if time.monotonic()>deadline:raise TimeoutError('缓存批量读回不完整')
            for j in range(64):
                b=reply[j*8:(j+1)*8]
                if b[:3]!=bytes([197,2,0x3b]) or b[5:]!=bytes([92,197,197]):
                    raise RuntimeError('缓存响应帧错位，拒绝使用该段数据')
                raw[i+j]=b[3]+256*b[4]
            if i%512==0:progress(f'读取缓存 {i/len(raw):.0%}')
        if self.read(0x3d)!=seq:raise RuntimeError('读取期间缓存被其他程序覆盖')
        return raw.astype(float)/4,dict(fs=fs,raw_fs=self.raw_fs,decimation=2**decim_log,
                 sequence=seq,status=state,encoding='unsigned ADC codes with 2 fractional bits')

    def close(self):
        try:
            self.write(0x34,2)
            self.inject(0,0)
            self.write(0x30,0)
            self.write(0x28,self.original_stream)
        finally:self.s.close()
