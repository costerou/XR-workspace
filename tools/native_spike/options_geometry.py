"""Options glass coordinates, shared with the native display renderer."""
W, H = 600, 440
HEADER_H, TABS_TOP, TABS_H = 44, 44, 36
BODY_TOP, BODY_BOTTOM, ROW_H, PAD, FOOTER_TOP = 88, 392, 38, 16, 400
GEAR_W, GEAR_H, GEAR_GAP, HIT_X, HIT_Y = 28, 14, 12, 4, 4
TRACK_X0, TRACK_X1 = 300, 450
# Header controls (x ranges) and the tab cells, as render_options_panel draws them (spike options_geometry.hpp).
SCOPE_X0, NEXT_X0, CLOSE_X0, CLOSE_X1 = 346, 440, 530, 594
TABS_X, TAB_W, TAB_PITCH, TABS_PER_PAGE = 16, 136, 142, 4

def range_value(row, x):
    value=row['minimum']+min(max((x-TRACK_X0)/(TRACK_X1-TRACK_X0),0),1)*(row['maximum']-row['minimum'])
    if row['step']>0: value=row['minimum']+round((value-row['minimum'])/row['step'])*row['step']
    return min(max(value,row['minimum']),row['maximum'])

def panel_rect(tile, pill):
    return tile[0] + (tile[2] - W) / 2, pill[1], W, H

def gear_rect(pill):
    return pill[0] + pill[2] + GEAR_GAP, pill[1], GEAR_W, GEAR_H

def inside(rect, point):
    return point is not None and rect[0] <= point[0] < rect[0]+rect[2] and rect[1] <= point[1] < rect[1]+rect[3]

def gear_hit(pill, point):
    x,y,w,h = gear_rect(pill)
    return inside((x-HIT_X,y-HIT_Y,w+2*HIT_X,h+2*HIT_Y), point)

def header_at(x, y):
    """'scope', 'next', 'close' or None at panel point (x, y)."""
    if not 0 <= y < HEADER_H: return None
    return 'scope' if SCOPE_X0 <= x < NEXT_X0 else 'next' if NEXT_X0 <= x < CLOSE_X0 else 'close' if CLOSE_X0 <= x < CLOSE_X1 else None

def tab_at(x, y, category, count):
    """The tab index drawn at panel point (x, y) with tab `category` open, or None (the gaps, and past the last tab)."""
    if not TABS_TOP <= y < TABS_TOP+TABS_H or x < TABS_X: return None
    cell = int((x-TABS_X)//TAB_PITCH)
    index = category//TABS_PER_PAGE*TABS_PER_PAGE + cell
    return index if cell < TABS_PER_PAGE and x-TABS_X-cell*TAB_PITCH < TAB_W and index < count else None

def row_at(x, y, scroll, count):
    if not PAD <= x < W-PAD or not BODY_TOP <= y < BODY_BOTTOM: return None
    index = int((y-BODY_TOP+scroll)//ROW_H)
    return index if 0 <= index < count else None

def row_rect(index, scroll):
    return PAD, BODY_TOP + index*ROW_H-scroll, W-2*PAD, ROW_H
