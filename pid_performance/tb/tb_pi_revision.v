`timescale 1ns/1ps
module tb_pi_revision;
reg clk=0; always #10 clk=~clk;
reg rst=0,tick=0,en=1,clr=0,load=0,dir=1,freeze=0;
reg [13:0] y=8192,sp=8192,bias=8192,lo=0,hi=16383;
reg [4:0] kp=6; reg [5:0] ki=12;
reg [31:0] lim=2097152;
wire [13:0] u; wire signed [15:0] err; wire signed [31:0] integ;
wire il,ul;
pi_ctrl dut(clk,rst,tick,en,clr,load,y,sp,bias,kp,ki,lim,lo,hi,dir,freeze,u,err,integ,il,ul);
integer checks=0,n,seed=12345,j;
longint signed ri=0,ru=8192,re=0,l,h,low,ib,e,pp,di,uu,ii,act;
task cycle;
begin
  l=lim>2147483647 ? 2147483647 : lim; h=hi<lo ? lo : hi; low=lo;
  ib=ri>l?l:ri < -l ? -l:ri;
  ii=ib; uu=ru;
  if(load) begin ii=0; uu=longint'(bias)>h?h:longint'(bias)<low?low:longint'(bias); re=0; end
  else if(clr) ii=0;
  else if(tick && en && !freeze) begin
    e=longint'(sp)-longint'(y); re=e;
    pp=kp>=15?0:e/(64'sd1<<kp);
    di=ki>=23?0:(e*512)/(64'sd1<<ki);
    act=dir?di:-di;
    uu=longint'(bias)+(dir?1:-1)*(pp+ib/512);
    if(!((uu>=h && act>0)||(uu<=low && act<0))) ii=ib+di;
    if(ii>l) ii=l; if(ii < -l) ii=-l;
    if(uu>h)uu=h; if(uu<low)uu=low;
  end
  if(ru>h)uu=h; else if(ru<low)uu=low;
  ri=ii;ru=uu;
  @(posedge clk); #1;
  if(integ !== ri[31:0] || u !== ru[13:0] || err !== re[15:0])
    $fatal(1,"scoreboard n=%0d I=%0d expected=%0d U=%0d expected=%0d E=%0d expected=%0d",checks,integ,ri,u,ru,err,re);
  checks=checks+1;
  @(negedge clk);
end
endtask
task reset_i;
begin load=1;cycle;load=0; end
endtask
initial begin
  #1;rst=0; #24;rst=1; @(negedge clk);
  // Exact full-error range and symmetric truncation, gain-off codes.
  tick=1;
  for(j=0;j<2;j=j+1) begin
    dir=j; sp=16383;y=0;kp=0;ki=0;reset_i;repeat(20)cycle;
    sp=0;y=16383;reset_i;repeat(20)cycle;
    kp=31;ki=63;reset_i;repeat(20)cycle;
  end
  // Strong I gain overflows a 32-bit sum before clamping; must not wrap.
  lo=0;hi=16383;kp=31;ki=0;lim=32'hffffffff;dir=1;
  sp=16383;y=0;reset_i;
  repeat(1000)cycle;
  // Both rails / both plant signs: sustained saturation must stop accumulation.
  lim=32'h7fffffff;lo=8000;hi=8400;kp=0;ki=4;
  for(j=0;j<2;j=j+1) begin
    dir=j;sp=10000;y=8192;reset_i;repeat(100)cycle;
    if(integ!=0)$fatal(1,"output saturation wound up integrator");
    sp=6000;repeat(100)cycle;
    if(integ!=0)$fatal(1,"opposite saturation wound up integrator");
  end
  // Random runtime limits, resets, disabled/frozen states, gaps and polarity.
  for(n=0;n<6000;n=n+1) begin
    sp=$random(seed);y=$random(seed);bias=$random(seed);
    kp=$random(seed);ki=$random(seed);
    dir=$random(seed);tick=$random(seed);en=$random(seed);freeze=($random(seed)&7)==0;
    load=($random(seed)&31)==0;clr=($random(seed)&31)==0;
    if(n%7==0)begin lo=$random(seed);hi=$random(seed);lim=$random(seed);end
    cycle;
  end
  // A first-order plant with either sign converges and recovers from a target change.
  lo=0;hi=16383;bias=8192;kp=3;ki=5;lim=8388608;
  tick=1;en=1;freeze=0;clr=0;load=0;
  for(j=0;j<2;j=j+1) begin
    dir=j; y=8192;sp=10000;reset_i;
    for(n=0;n<5000;n=n+1)begin
      cycle;
      act=8192+(dir?1:-1)*(longint'(u)-8192);
      y=longint'(y)+(act-longint'(y))/8;
    end
    if(y<9990 || y>10010)$fatal(1,"plant convergence failed: dir=%0d y=%0d",dir,y);
    sp=6500;
    for(n=0;n<5000;n=n+1)begin cycle;act=8192+(dir?1:-1)*(longint'(u)-8192);y=longint'(y)+(act-longint'(y))/8;end
    if(y<6490 || y>6510)$fatal(1,"plant recovery failed: dir=%0d y=%0d",dir,y);
  end
  $display("REVISION_PASS tb_pi_revision checks=%0d",checks);$finish;
end
initial begin #10000000;$fatal(1,"timeout");end
endmodule
