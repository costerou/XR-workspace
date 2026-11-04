"""Options glass coordinates, shared with the native display renderer."""
W, H = 600, 440
HEADER_H, TABS_TOP, TABS_H = 44, 44, 36
BODY_TOP, BODY_BOTTOM, ROW_H, PAD, FOOTER_TOP = 88, 392, 38, 16, 400
GEAR_W, GEAR_H, GEAR_GAP, HIT_X, HIT_Y = 28, 14, 12, 4, 4

def panel_rect(tile, pill):
    return tile[0] + (tile[2] - W) / 2, pill[1], W, H

def gear_rect(pill):
    return pill[0] + pill[2] + GEAR_GAP, pill[1], GEAR_W, GEAR_H

def inside(rect, point):
    return point is not None and rect[0] <= point[0] < rect[0]+rect[2] and rect[1] <= point[1] < rect[1]+rect[3]

def gear_hit(pill, point):
    x,y,w,h = gear_rect(pill)
    return inside((x-HIT_X,y-HIT_Y,w+2*HIT_X,h+2*HIT_Y), point)

def row_at(x, y, scroll, count):
    if not PAD <= x < W-PAD or not BODY_TOP <= y < BODY_BOTTOM: return None
    index = int((y-BODY_TOP+scroll)//ROW_H)
    return index if 0 <= index < count else None

def row_rect(index, scroll):
    return PAD, BODY_TOP + index*ROW_H-scroll, W-2*PAD, ROW_H
