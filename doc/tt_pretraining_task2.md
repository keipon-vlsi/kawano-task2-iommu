**tenstorrent** 

Pre-training Program 2: IOMMU Microarchitecture Exploration 

1. Scope of Work 

* Become familiar with performance sensitivity (e.g., cache architecture) 



2. Expected outcomes 

* A report detailing the performance requirements for the IOMMU and a proposed IOMMU microarchitecture to meet these targets. 


* Simulator and simulation results 



3. Report Contains: 

* Latency of address translation without any caching. 


* Latency of address translation with some cache structures (i.e. IOATCs, such as IOTLB, DDT cache, PDT cache, MSI Trans. cache) 


* The number of parallel page table walk capabilities required to sustain wire rate, assuming an infinite transaction buffer at the I/O bridge. 


* Minimum buffer requirements to sustain wire rate, assuming sufficient parallel page table walk capability. 



4. Simulator 

* The simulator created here simulates the IOMMU's caching mechanisms and returns performance metrics (e.g., hit/miss rates) for a sample trace of data transfer requests. 



5. Conditions 

* The IOMMU must support an 800GbE Ethernet controller connected via PCIe Gen 6 x16 (128GB/s), continuously transferring 4kB memory pages to a continuous memory address. 


* IOMMU clock frequency: 400 MHz. 


* Main memory system connected via AXI, with a memory access latency of 100 ns. 



---

**ADIP/Tenstorrent Confidential** 
Apr. 1st, 2026