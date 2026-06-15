// =====================================================================
// iommu_cache_pkg : アドレス界・page_size・スーパーページ用ヘルパ
// =====================================================================
package iommu_cache_pkg;
  localparam int PA_W = 56, GPA_W = 41, IOVA_W = 39;

  typedef enum logic [1:0] {PS_4K=2'd0, PS_2M=2'd1, PS_1G=2'd2} ps_e; // 値が小=細

  // IOVA[38:12]=27bit VPN : [8:0]=VPN0(iova[20:12]), [17:9]=VPN1, [26:18]=VPN2
  // GPA [40:12]=29bit GVPN: [8:0]=GVPN0, [17:9]=GVPN1, [28:18]=GVPN2(11b)
  function automatic logic [26:0] vpn_of (input logic [IOVA_W-1:0] a); vpn_of = a[38:12]; endfunction
  function automatic logic [28:0] gvpn_of(input logic [GPA_W-1:0]  a); gvpn_of= a[40:12]; endfunction

  // --- スーパーページ対応:可変マスクのタグ比較(IOTLB用) ---
  // psizeで下位フィールドを無視して比較する
  function automatic logic vpn_match(input logic[26:0] r, input logic[26:0] e, input ps_e ps);
    unique case (ps)
      PS_4K: vpn_match = (r          == e);            // VPN[2:0] 全比較
      PS_2M: vpn_match = (r[26:9]    == e[26:9]);      // VPN[0]無視 (2MB)
      PS_1G: vpn_match = (r[26:18]   == e[26:18]);     // VPN[1:0]無視 (1GB)
      default: vpn_match = 1'b0;
    endcase
  endfunction

  // --- スーパーページ対応:SPA合成(素通り幅をpsizeで可変) ---
  // base は page-aligned (下位ゼロ)。 SPA = base上位 | iova下位
  function automatic logic [PA_W-1:0] compose_spa
      (input logic [PA_W-1:0] base, input logic [IOVA_W-1:0] iova, input ps_e ps);
    unique case (ps)
      PS_4K: compose_spa = {base[PA_W-1:12], iova[11:0]};   // 12bit 素通り
      PS_2M: compose_spa = {base[PA_W-1:21], iova[20:0]};   // 21bit
      PS_1G: compose_spa = {base[PA_W-1:30], iova[29:0]};   // 30bit
      default: compose_spa = '0;
    endcase
  endfunction

  function automatic ps_e ps_min(input ps_e a, input ps_e b); // eff = min(Pv,Pg)
    ps_min = (a < b) ? a : b;
  endfunction
endpackage

// ===============================================================================================

import iommu_cache_pkg::*;

// (1) 結合IOTLB : IOVA -> data SPA。タグ=VPN[2:0]、eff=min(Pv,Pg)。可変マスク比較。
typedef struct packed {
  logic              valid;
  ps_e               psize;            // ★eff page_size(4K/2M/1G) = マスク幅
  logic [26:0]       vpn;              // タグ: IOVA[38:12]
  logic [PA_W-1:12]  spa_base;         // page base SPA(下位ゼロ)
  // 1プロセス1デバイス特化 → device_id/PASID/VMID タグは省略(入口比較器で一致確認)
} iotlb_e;

// (2) VM結合PWC(VM-L2/VM-L1) : タグ=VPNのprefix。leafビットで“中間 or VMスーパーページ”を判別。
//     値は G-resolved。!leaf→次VM表SPA / leaf→data GPA(分離G PWCへ回す)
typedef struct packed {
  logic              valid;
  logic              leaf;             // ★1=このレベルでVMスーパーページ(値=data GPA)
  ps_e               psize;            // leaf時のVMページサイズ
  logic [PREFIX_W-1:0] vpn_pfx;        // VM-L2:VPN[2](9b) / VM-L1:VPN[2:1](18b)
  logic [PA_W-1:12]    next_tbl_spa;   // !leaf: 次VM表のSPA
  logic [GPA_W-1:12]   data_gpn;       // leaf : data GPAのページ番号
} vm_pwc_e #(parameter int PREFIX_W);

// (3) 分離G PWC(G-L2@VM-L0 / G-L1@VM-L0) : タグ=data GPAのGVPN prefix。G-stageはflat。
typedef struct packed {
  logic              valid;
  logic              leaf;             // ★1=このレベルでGスーパーページ(値=data SPA)
  ps_e               psize;            // leaf時のGページサイズ
  logic [GPREFIX_W-1:0] gvpn_pfx;      // G-L2:GVPN[2](11b) / G-L1:GVPN[2:1](20b)
  logic [PA_W-1:12]     next_gtbl_spa; // !leaf: 次G表のSPA
  logic [PA_W-1:12]     spa_base;      // leaf : data SPA base(Gスーパーページ)
} g_pwc_e #(parameter int GPREFIX_W);