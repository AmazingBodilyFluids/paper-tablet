"""Write calibration sheets: crosshairs at 10% insets, page aspect = Paper Pro screen (3:4)."""
import sys
def sheet(path, landscape):
    W, H = (800.0, 600.0) if landscape else (600.0, 800.0)
    ops = ["1 w 0 0 0 RG 0 0 0 rg"]
    ticks = [0.1, 0.5, 0.9]
    pts = [(u, v, str(i + 1)) for i, (u, v) in enumerate((u, v) for v in ticks for u in ticks)]
    for fx, fy, lab in pts:
        x, y = fx * W, H - fy * H  # PDF origin bottom-left
        ops += [f"{x-28:.1f} {y:.1f} m {x+28:.1f} {y:.1f} l S", f"{x:.1f} {y-28:.1f} m {x:.1f} {y+28:.1f} l S",
                f"{x-10:.1f} {y-10:.1f} 20 20 re S",
                f"BT /F1 18 Tf {x+16:.1f} {y+(-36 if fy > 0.6 else 22):.1f} Td ({lab}) Tj ET"]
    txt = ["Calibration sheet: open full screen, no zoom.",
           "Tap the crosshair centres in the order the PC app asks."]
    for i, t in enumerate(txt):
        ops.append(f"BT /F1 12 Tf {W/2-150:.1f} {H*0.3-18*i:.1f} Td ({t}) Tj ET")
    content = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W:.0f} {H:.0f}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(),
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = b"%PDF-1.4\n"; offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer << /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    open(path, "wb").write(out)
sheet(sys.argv[1] + "/calibration-landscape.pdf", True)
sheet(sys.argv[1] + "/calibration-portrait.pdf", False)
