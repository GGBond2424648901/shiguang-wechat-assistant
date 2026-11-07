Option Explicit
Dim fs, shell, folder, pythonw, installed, installedPanel, localPanel, localReady, launchLegacy, args
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.BuildPath(folder, ".venv\Scripts\pythonw.exe")
installed = fs.BuildPath(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), "Programs\XiaomaoAssistant")
installedPanel = fs.BuildPath(installed, "desktop\ShiguangDesktop.exe")
localPanel = fs.BuildPath(folder, "desktop\dist\index.html")
localReady = fs.FileExists(localPanel) And fs.FileExists(pythonw) And _
             (fs.FileExists(fs.BuildPath(folder, "desktop\node_modules\electron\dist\electron.exe")) Or _
              fs.FileExists(shell.ExpandEnvironmentStrings("%SHIGUANG_ELECTRON%")))
launchLegacy = WScript.Arguments.Named.Exists("legacy")
shell.CurrentDirectory = folder
If Not localReady And fs.FileExists(installedPanel) And _
   fs.FileExists(fs.BuildPath(installed, "Xiaomao.exe")) And _
   fs.FileExists(fs.BuildPath(installed, "desktop\resources\app\dist\index.html")) Then
  args = " --root " & Quote(folder) & " gui"
  If launchLegacy Then args = args & " --legacy-ui"
  shell.Run Quote(fs.BuildPath(installed, "Xiaomao.exe")) & args, 0, False
  WScript.Quit 0
End If
If Not fs.FileExists(pythonw) Then
  MsgBox "Run the dependency installer first.", 48, "Shiguang WeChat Assistant"
  WScript.Quit 1
End If
args = " -m xiaomao --root " & Quote(folder) & " gui"
If launchLegacy And localReady Then args = args & " --legacy-ui"
shell.Run Quote(pythonw) & args, 0, False

Function Quote(value)
  Quote = Chr(34) & value & Chr(34)
End Function
