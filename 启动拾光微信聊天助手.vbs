Option Explicit
Dim fs, shell, folder, pythonw, installed, localReady, launchLegacy, args, executable, requiredVersion
Set fs = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
pythonw = fs.BuildPath(folder, ".venv\Scripts\pythonw.exe")
installed = fs.BuildPath(shell.ExpandEnvironmentStrings("%LOCALAPPDATA%"), "Programs\XiaomaoAssistant")
requiredVersion = "0.3.12"
launchLegacy = WScript.Arguments.Named.Exists("legacy")
shell.CurrentDirectory = folder
If BundleReady(installed) Then
  executable = fs.BuildPath(installed, "Xiaomao.exe")
  args = " --root " & Quote(folder) & " gui"
  If fs.FileExists(fs.BuildPath(folder, "runtime\config.json")) Then args = args & " --remember-profile"
Else
  localReady = fs.FileExists(pythonw) And fs.FileExists(fs.BuildPath(folder, "desktop\dist\index.html")) And _
               MarkerReady(fs.BuildPath(folder, "desktop\dist\build-version.txt")) And _
               (fs.FileExists(fs.BuildPath(folder, "desktop\node_modules\electron\dist\electron.exe")) Or _
                fs.FileExists(shell.ExpandEnvironmentStrings("%SHIGUANG_ELECTRON%")))
  If Not localReady And Not (launchLegacy And fs.FileExists(pythonw)) Then
    If WScript.Arguments.Named.Exists("check") Then
      WScript.Echo "ERROR: New desktop is missing or older than " & requiredVersion
    Else
      MsgBox "Please install Shiguang " & requiredVersion & " or newer, or rebuild desktop. Your settings are unchanged. The old UI is available only with /legacy.", 48, "Shiguang WeChat Assistant"
    End If
    WScript.Quit 1
  End If
  executable = pythonw
  args = " -m xiaomao --root " & Quote(folder) & " gui"
End If
If launchLegacy Then args = args & " --legacy-ui"
If WScript.Arguments.Named.Exists("check") Then
  WScript.Echo Quote(executable) & args
Else
  shell.Run Quote(executable) & args, 0, False
End If

Function VersionAtLeast(actual, minimum)
  Dim a, b, i, av, bv
  VersionAtLeast = False
  a = Split(actual, ".") : b = Split(minimum, ".")
  If UBound(a) < 2 Then Exit Function
  For i = 0 To UBound(a)
    If Not IsNumeric(a(i)) Then Exit Function
  Next
  For i = 0 To 2
    av = CLng(a(i)) : bv = CLng(b(i))
    If av > bv Then VersionAtLeast = True : Exit Function
    If av < bv Then Exit Function
  Next
  VersionAtLeast = True
End Function

Function MarkerReady(file)
  Dim stream, version
  MarkerReady = False
  If Not fs.FileExists(file) Then Exit Function
  Set stream = fs.OpenTextFile(file, 1)
  version = Trim(stream.ReadAll) : stream.Close
  MarkerReady = VersionAtLeast(version, requiredVersion)
End Function

Function BundleReady(directory)
  Dim app, panel
  BundleReady = False
  app = fs.BuildPath(directory, "Xiaomao.exe")
  panel = fs.BuildPath(directory, "desktop\ShiguangDesktop.exe")
  If Not fs.FileExists(app) Or Not fs.FileExists(panel) Then Exit Function
  If Not fs.FileExists(fs.BuildPath(directory, "desktop\resources\app\dist\index.html")) Then Exit Function
  BundleReady = VersionAtLeast(fs.GetFileVersion(app), requiredVersion) And _
                VersionAtLeast(fs.GetFileVersion(panel), requiredVersion) And _
                MarkerReady(fs.BuildPath(directory, "desktop\resources\app\dist\build-version.txt"))
End Function

Function Quote(value)
  Quote = Chr(34) & value & Chr(34)
End Function
