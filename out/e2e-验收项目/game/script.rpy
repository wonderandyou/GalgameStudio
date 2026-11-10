# 由鲸落工坊 mock 后端生成（演示用）
# 资源为 1px 占位图 / 1 秒静音，接真模型后替换即可

label start:
    jump n1

label n1:
    scene bg bg1
    voice "voice/n1_01.wav"
    "十一点四十分，天台的门没锁。"
    jump n2

label n2:
    scene bg bg2
    voice "voice/n2_01.wav"
    "她" "你迟到了七分钟。"
    jump n3

label n3:
    scene bg bg3
    voice "voice/n3_01.wav"
    "她" "票我买好了。你要走吗？"
    menu:
        "「走。」":
            jump n4
        "「……回去吧。」":
            jump n5

label n4:
    scene bg bg4
    voice "voice/n4_01.wav"
    "她" "她没说话，只把票塞进我手里。"
    jump n6

label n5:
    scene bg bg5
    voice "voice/n5_01.wav"
    "她" "那我把票退了。"
    jump n7

label n6:
    scene bg bg6
    voice "voice/n6_01.wav"
    "我们谁都没有回头。"
    # 真结局
    return

label n7:
    scene bg bg7
    voice "voice/n7_01.wav"
    "雨一直下到天亮。"
    # 坏结局
    return
