import os, subprocess, sys
flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
env = dict(os.environ)
env["GAL_MOCK"] = sys.argv[2]
subprocess.Popen(
    [sys.argv[1], "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", sys.argv[3]],
    cwd=r"D:\软件\GalgameStudio", env=env,
    stdout=open(sys.argv[4], "ab"), stderr=subprocess.STDOUT,
    creationflags=flags)
print("spawned")
