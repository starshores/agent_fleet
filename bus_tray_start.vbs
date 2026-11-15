Option Explicit
' Start bus_tray.py hidden, without hard-coded machine paths.
' Repo root = AGENTBUS_AGENT_FLEET_HOME, else this script's folder.
' Python  = AGENTBUS_PYTHONW_BIN, else pythonw next to AGENTBUS_PYTHON_BIN, else pythonw.exe on PATH.
Dim shell, fso, home, script, pyw, py
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

home = shell.ExpandEnvironmentStrings("%AGENTBUS_AGENT_FLEET_HOME%")
If home = "%AGENTBUS_AGENT_FLEET_HOME%" Then home = ""
If home = "" Then home = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(home, "bus_tray.py")

pyw = shell.ExpandEnvironmentStrings("%AGENTBUS_PYTHONW_BIN%")
If pyw = "%AGENTBUS_PYTHONW_BIN%" Then pyw = ""
If pyw = "" Then
  py = shell.ExpandEnvironmentStrings("%AGENTBUS_PYTHON_BIN%")
  If py = "%AGENTBUS_PYTHON_BIN%" Then py = ""
  If py <> "" Then
    pyw = fso.BuildPath(fso.GetParentFolderName(py), "pythonw.exe")
    If Not fso.FileExists(pyw) Then pyw = py
  End If
End If
If pyw = "" Then pyw = "pythonw.exe"

shell.CurrentDirectory = home
shell.Run """" & pyw & """ """ & script & """", 0, False
