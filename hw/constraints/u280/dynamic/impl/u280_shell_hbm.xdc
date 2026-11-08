# HBM (100 MHz)
set_property	PACKAGE_PIN	BJ44		        [get_ports 	hbm_clk_clk_n] ; 
set_property	PACKAGE_PIN	BJ43		        [get_ports 	hbm_clk_clk_p] ; 
set_property IOSTANDARD  LVDS 	  [get_ports hbm_clk_clk_n] ;
set_property IOSTANDARD  LVDS 	  [get_ports hbm_clk_clk_p] ;
set_property DQS_BIAS    TRUE [get_ports hbm_clk_clk_p] ;