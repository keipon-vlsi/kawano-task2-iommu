# KLayout batch layout render: GDS -> PNG (headless, no GUI/X needed).
# Run: klayout -z -rd in_gds=<gds> -rd out_png=<png> -rm render_layout.py
import pya

in_gds = globals().get("in_gds")
out_png = globals().get("out_png")

lv = pya.LayoutView()
lv.load_layout(in_gds, True)   # add_cellview=True
lv.max_hier()
lv.zoom_fit()
lv.save_image(out_png, 1400, 1400)
print("##PNG_WRITTEN " + out_png)
