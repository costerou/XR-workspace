"""Options glass coordinates, shared with the native display renderer."""
# Mirrors spike/include/xr/options_geometry.hpp, itself the placement panel's constants (glass.hpp); options_geometry_test dumps them.
EXTRA_W, PANEL_W, PANEL_H = 80, 480, 394
W, H = PANEL_W + EXTRA_W, PANEL_H
PAD, HEADER_H, ROW_H, ROWS_TOP, KNOB_R, BOX_H, LIST_GAP, ITEM_H = 22, 42, 34, 46, 7, 26, 2, 23
SECTION_TOP, SCOPE_TOP = ROWS_TOP, ROWS_TOP + ROW_H
BOX_X0, BOX_X1 = 184, W - PAD
DIVIDER_Y = SCOPE_TOP + ROW_H + 3
BODY_TOP = DIVIDER_Y + 5
VISIBLE_ROWS = 7
BODY_H = VISIBLE_ROWS * ROW_H
BODY_BOTTOM = BODY_TOP + BODY_H
FOOTER_TOP = 364
TRACK_X1 = W - PAD - (PANEL_W - PAD - 374)
TRACK_X0 = TRACK_X1 - 122
KNOB_LO, KNOB_HI = TRACK_X0 + KNOB_R, TRACK_X1 - KNOB_R
CLOSE_W, CLOSE_H = 74, BOX_H
CLOSE_X1 = W - PAD
CLOSE_X0 = CLOSE_X1 - CLOSE_W
CLOSE_Y0 = (8 + 5 + HEADER_H - CLOSE_H - 1) / 2   # on the header's text line (kGrabberTop, kGrabberH)
GEAR_GAP, HIT_X, HIT_Y = 12, 4, 4
SECTIONS, LIST_ROWS = 14, 7
LIST_TOP = SECTION_TOP + (ROW_H + BOX_H) / 2 + LIST_GAP
LIST_ITEM_W = (BOX_X1 - BOX_X0) / 2
CHOICE_ROWS, CHOICE_ITEM_H, CHOICE_GAP = 6, 20, 2
CHOICE_ITEM_W = BOX_X1 - TRACK_X0
# OptionsMenu focus: a row, or one of these header controls.
FOCUS_SCOPE, FOCUS_SECTION, FOCUS_LIST, FOCUS_CLOSE = -4, -3, -2, -1

def range_value(row, x):
    value=row['minimum']+min(max((x-KNOB_LO)/(KNOB_HI-KNOB_LO),0),1)*(row['maximum']-row['minimum'])
    if row['step']>0: value=row['minimum']+round((value-row['minimum'])/row['step'])*row['step']
    return min(max(value,row['minimum']),row['maximum'])

def panel_rect(tile, pill):
    return tile[0] + (tile[2] - W) / 2, pill[1], W, H

def gear_rect(pill):
    """The Options pill: the drag pill's twin, GEAR_GAP to its right."""
    return pill[0] + pill[2] + GEAR_GAP, pill[1], pill[2], pill[3]

def inside(rect, point):
    return point is not None and rect[0] <= point[0] < rect[0]+rect[2] and rect[1] <= point[1] < rect[1]+rect[3]

def gear_hit(pill, point):
    x,y,w,h = gear_rect(pill)
    return inside((x-HIT_X,y-HIT_Y,w+2*HIT_X,h+2*HIT_Y), point)

def section_box():
    return BOX_X0, SECTION_TOP + (ROW_H - BOX_H) / 2, BOX_X1 - BOX_X0, BOX_H

def scope_box():
    return BOX_X0, SCOPE_TOP + (ROW_H - BOX_H) / 2, BOX_X1 - BOX_X0, BOX_H

def close_box():
    return CLOSE_X0, CLOSE_Y0, CLOSE_W, CLOSE_H

def header_at(x, y):
    """'close', 'section', 'monitor', 'workspace' (the scope capsule's halves) or None at panel point (x, y)."""
    if inside(close_box(), (x, y)): return 'close'
    if inside(section_box(), (x, y)): return 'section'
    if inside(scope_box(), (x, y)): return 'monitor' if x < BOX_X0 + (BOX_X1 - BOX_X0) / 2 else 'workspace'
    return None

def section_item(index):
    """Item `index` of the open section list: two columns, top down."""
    return BOX_X0 + index // LIST_ROWS * LIST_ITEM_W, LIST_TOP + index % LIST_ROWS * ITEM_H, LIST_ITEM_W, ITEM_H

def section_at(x, y):
    """The section at panel point (x, y) of the open list, or None."""
    return next((i for i in range(SECTIONS) if inside(section_item(i), (x, y))), None)

def row_top(index, scroll):
    return BODY_TOP + index * ROW_H - scroll

def choice_item(row_y, count, index):
    """Item `index` of a choice row's list, its row's top at `row_y` (options_geometry.hpp options_choice_item)."""
    columns = 2 if count > CHOICE_ROWS else 1
    rows = (count + columns - 1) // columns
    box = row_y + (ROW_H - BOX_H) / 2
    height = rows * CHOICE_ITEM_H
    above, below = box - CHOICE_GAP - height, box + BOX_H + CHOICE_GAP
    top = above if above >= HEADER_H else below if below + height <= H - CHOICE_GAP else H - CHOICE_GAP - height
    return BOX_X1 - (columns - index // rows) * CHOICE_ITEM_W, top + index % rows * CHOICE_ITEM_H, CHOICE_ITEM_W, CHOICE_ITEM_H

def choice_at(x, y, row_y, count):
    """The item of the open list at panel point (x, y), or None."""
    return next((i for i in range(count) if inside(choice_item(row_y, count, i), (x, y))), None)

def row_at(x, y, scroll, count):
    if not PAD <= x < W-PAD or not BODY_TOP <= y < BODY_BOTTOM: return None
    index = int((y-BODY_TOP+scroll)//ROW_H)
    return index if 0 <= index < count else None

def row_rect(index, scroll):
    return PAD, BODY_TOP + index*ROW_H-scroll, W-2*PAD, ROW_H
