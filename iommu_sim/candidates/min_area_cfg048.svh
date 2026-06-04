// Auto-generated IOMMU parameters from simulator config 'cfg048'
// selection: min_area (cfg048): area=15841 GE, E/xlate=167.58
// mode=nested superpage=off lookup=hybrid prefetch=stride
`ifndef IOMMU_PARAMS_SVH
`define IOMMU_PARAMS_SVH

localparam int CLOCK_MHZ            = 400;
localparam int MEM_LATENCY_CYCLES   = 40;
localparam int COALESCE_FACTOR      = 8;

// 0 == fully associative (CAM)
localparam int IOTLB_ENTRIES        = 32;
localparam int IOTLB_ASSOC          = 4;
localparam int S1_PWC_L2_ENTRIES    = 4;
localparam int S1_PWC_L1_ENTRIES    = 8;
localparam int S2_PWC_ENTRIES       = 8;
localparam int TABLE_GPA_ENTRIES    = 16;
localparam bit DATA_GPA_ENABLED     = 0;
localparam int DATA_GPA_ENTRIES     = 64;
localparam int DDTC_ENTRIES         = 16;
localparam bit PDTC_ENABLED         = 0;
localparam int PDTC_ENTRIES         = 16;
localparam int MSI_ENTRIES          = 16;

localparam int NUM_WALKERS          = 2;
localparam int WALK_PIPELINE_DEPTH  = 2;
localparam int IOMMU_REQ_BUFFER     = 8;
localparam int IO_BRIDGE_BUFFER     = 16;
localparam int LOOKUP_CYCLES        = 2;
localparam int ARBITRATION_CYCLES   = 1;
localparam int HIT_LATENCY_CYCLES   = 1;

`endif // IOMMU_PARAMS_SVH
