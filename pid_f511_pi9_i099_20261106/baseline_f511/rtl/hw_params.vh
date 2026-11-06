// Compile-time width/default constants. Runtime defaults live in reg_file.v.
// ADC pins are two's complement; adc_drv normalizes format and polarity once.
// DDS control is analog through the DAC; no digital DDS bit dropping is applied.
`ifndef HW_PARAMS_VH
`define HW_PARAMS_VH
`define SYS_CLK_HZ              50_000_000
`define SYS_CLK_PERIOD_NS       20.000
`define ADC_HALF_DEFAULT        4
`define ADC_HALF_MIN            0
`define ADC_CODE_MID            14'h2000
`define ADC_CODE_MAX            14'h3FFF
`define ADC_CODE_MIN            14'h0000
`define ADC_CODE_FS             14'h3FFF
`define DAC_CODE_MID            14'h2000
`define DAC_CODE_MAX            14'h3FFF
`define DAC_CODE_MIN            14'h0000
// Historical board calibration: V=(code-8198)*0.0006533 V.
// Integer endpoints 545..15851 are nominal -4.999705..+4.999705 V.
// Actual analog voltage requires measurement; this is a digital bound.
`define DAC_VCO_MIN             14'd545
`define DAC_VCO_MAX             14'd15851
`define DDS_CTRL_BITS           13
`define DDS_CTRL_DROP_LSB       1'b1
`define SP_CODE_DEFAULT         14'h2000
`define BIAS_CODE_DEFAULT       14'h2000
`define KP_SHIFT_DEFAULT        6
`define KI_SHIFT_DEFAULT        12
`define I_CLAMP_DEFAULT         32'h0020_0000
`define OUT_MIN_DEFAULT         14'h0000
`define OUT_MAX_DEFAULT         14'h3FFF
`define LIGHT_LOSS_TH_DEFAULT   16
`define STATS_LOG2_DEFAULT      12
`define CTRL_HALF_DEFAULT       499
`define CTRL_HALF_MAX           16'hFFFF
`endif
