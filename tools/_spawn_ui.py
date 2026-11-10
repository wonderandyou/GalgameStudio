import subprocess, sys, os
flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
subprocess.Popen(
    [sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]],
    cwd=sys.argv[4],
    stdout=open(r"D:\软件\GalgameStudio\ui_server.log", "ab"), stderr=subprocess.STDOUT,
    creationflags=flags)
print("spawned")
