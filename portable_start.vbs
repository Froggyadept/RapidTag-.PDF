' RapidTag hidden launcher: EXE first, source launcher second.
Option Explicit
Dim fso, sh, cur, py, launcher
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
cur = fso.GetParentFolderName(WScript.ScriptFullName)
py = "pythonw.exe"
launcher = cur & "\launcher.py"
sh.CurrentDirectory = cur
If fso.FileExists(launcher) Then
    sh.Run py & " " & Chr(34) & launcher & Chr(34), 0, False
Else
    MsgBox "launcher.py was not found.", vbExclamation, "RapidTag"
End If
