from PIL import Image
import collections

shot = r"D:\软件\GalgameStudio\tools\shots\game_05.png"
im = Image.open(shot).convert("RGB")
print("shot:", im.size)
w, h = im.size
for y in [10, int(h*0.3), int(h*0.6), int(h*0.72), int(h*0.78), int(h*0.85), int(h*0.92), h-5]:
    px = [im.getpixel((x, y)) for x in range(0, w, max(1,w//10))]
    uniq = sorted(set(px))
    print("y=%5d  不同色数=%3d  样例=%s" % (y, len(uniq), px[:5]))

print("")
print("=== 全图颜色统计 top8 ===")
small = im.resize((200,125))
cnt = collections.Counter(small.getdata())
for c, n in cnt.most_common(8):
    print("  %s  %.1f%%" % (str(c), n*100.0/(200*125)))

print("")
print("=== mock.png ===")
mp = r"D:\软件\GalgameStudio\projects\p_63b2b758e0ab46d1bf4201629468eb36\assets\bg\mock.png"
m = Image.open(mp)
print("mode/size:", m.mode, m.size)
m2 = m.convert("RGBA")
mw, mh = m.size
for y in [0, mh//2, mh-1]:
    row = [m2.getpixel((x,y)) for x in range(mw)]
    print("  y=%3d 不同色数=%d 左=%s 中=%s 右=%s" % (y, len(set(row)), row[0], row[mw//2], row[-1]))