// graph.json 的数据结构 —— 字段名必须和 Python 那边导出的 JSON 完全一致，
// Unity 的 JsonUtility 是靠字段名反射的，差一个字母就读不到。
using System;

[Serializable]
public class GraphData {
    public string version;
    public ProjectData project;
    public CharacterData[] characters;
    public NodeData[] nodes;
}

[Serializable]
public class ProjectData {
    public string id;
    public string name;
}

[Serializable]
public class CharacterData {
    public string id;
    public string name;
    public string color;
    public string ref_image;
    public string voice_id;
}

[Serializable]
public class BgData {
    public string asset;
    public string prompt;
}

[Serializable]
public class PoseData {
    public string id;
    public string pose;      // 资产相对路径
    public string pos;       // left / center / right
    public string mood;
}

[Serializable]
public class LineData {
    public string who;
    public string text;
    public string voice;
    public float dur;
}

[Serializable]
public class ChoiceData {
    public string label;
    public string to;
    public string condition;
}

[Serializable]
public class NodeData {
    public string id;
    public string type;      // start / scene / choice / ending
    public string title;
    public string status;
    public string end_kind;  // true / bad / alt
    public BgData bg;
    public PoseData[] chars;
    public LineData[] lines;
    public ChoiceData[] choices;
}
