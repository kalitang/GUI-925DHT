' PowerCollector launcher (VBScript, pure ASCII)
' Writes a tiny pure-ASCII .cmd file then runs it via cmd.exe.
' Bypasses any "syntax error" issues caused by mixed-encoding bat files.

Option Explicit

Dim fso, ws, scriptPath, batPath, logFile, q
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")
q = Chr(34)   ' "

scriptPath = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptPath, "launch_hidden.cmd")
logFile = fso.BuildPath(scriptPath, "startup.log")

Dim log
Set log = fso.OpenTextFile(logFile, 8, True)
log.WriteLine "====== " & Now & " (launch.vbs) ======"
log.Close

Dim cmdBat
If Not fso.FileExists(batPath) Then
    ' 仅当 launch_hidden.cmd 缺失时才生成（正常情况下直接复用，
    ' 避免覆盖手工维护的相对路径版本）
    Set cmdBat = fso.CreateTextFile(batPath, True)
    cmdBat.WriteLine "@echo off"
    cmdBat.WriteLine "cd /d " & q & scriptPath & q
    cmdBat.WriteLine "echo PowerCollector v1.0 (one-click start)"
    cmdBat.WriteLine "echo Dir:   " & scriptPath
    cmdBat.WriteLine "echo Log:   " & logFile
    cmdBat.WriteLine "echo."
    cmdBat.WriteLine "where python >nul 2>&1"
    cmdBat.WriteLine "if errorlevel 1 ("
    cmdBat.WriteLine "    echo [X] Python not in PATH. Install from https://www.python.org/"
    cmdBat.WriteLine "    pause"
    cmdBat.WriteLine "    exit /b 1"
    cmdBat.WriteLine ")"
    cmdBat.WriteLine "python -c " & q & "import PySide6, xlrd, requests" & q & " 2>nul"
    cmdBat.WriteLine "if errorlevel 1 ("
    cmdBat.WriteLine "    echo [!] Installing dependencies..."
    cmdBat.WriteLine "    python -m pip install --disable-pip-version-check -r requirements.txt"
    cmdBat.WriteLine "    if errorlevel 1 goto :fail"
    cmdBat.WriteLine ")"
    cmdBat.WriteLine "if not exist web\index.html goto :fail"
    cmdBat.WriteLine "if not exist main.py goto :fail"
    cmdBat.WriteLine "echo === Launching main app ==="
    cmdBat.WriteLine "python -u main.py"
    cmdBat.WriteLine "if errorlevel 1 goto :fail"
    cmdBat.WriteLine "echo Done."
    cmdBat.WriteLine "exit /b 0"
    cmdBat.WriteLine ":fail"
    cmdBat.WriteLine "echo [X] Startup failed. See log: " & logFile
    cmdBat.WriteLine "pause"
    cmdBat.WriteLine "exit /b 1"
    cmdBat.Close
End If

Dim exitCode
exitCode = ws.Run("""" & batPath & """", 1, True)

If exitCode <> 0 Then
    MsgBox "PowerCollector failed to start." & vbCrLf & "Log: " & logFile, vbExclamation, "PowerCollector"
End If