Option Explicit
Dim fs, shell, folder, pythonw
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.BuildPath(folder, ".venv\Scripts\pythonw.exe")
If Not fs.FileExists(pythonw) Then
  MsgBox "Run the dependency installer first.", 48, "Shiguang WeChat Assistant"
  WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & pythonw & Chr(34) & " -m xiaomao gui", 0, False
