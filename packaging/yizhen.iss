#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef ReleaseDir
  #error ReleaseDir is required
#endif
#define Version "0.1.1"
[Setup]
AppId={{9D864B65-0931-48BA-B742-725E8A9CFE04}
AppName=译帧
AppVersion={#Version}
AppPublisher=xxsdd
AppPublisherURL=https://github.com/xxsdd/yizhen-video-translator
AppSupportURL=https://github.com/xxsdd/yizhen-video-translator/issues
DefaultDirName={code:GetDefaultDir}
DefaultGroupName=译帧
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
DisableDirPage=no
WizardStyle=modern
SetupIconFile={#PayloadDir}\assets\yizhen.ico
UninstallDisplayIcon={app}\assets\yizhen.ico
OutputDir={#ReleaseDir}
OutputBaseFilename=Yizhen-Setup-{#Version}-Windows-x64
Compression=lzma2/normal
SolidCompression=yes
LZMANumBlockThreads=4
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
InfoBeforeFile=install-info.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建译帧快捷方式"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "\data\*"
Source: "{#PayloadDir}\data\dictionaries\*"; DestDir: "{app}\data\dictionaries"; Flags: ignoreversion uninsneveruninstall

[Icons]
Name: "{group}\译帧"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launch.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\yizhen.ico"
Name: "{autodesktop}\译帧"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launch.py"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\yizhen.ico"; Tasks: desktopicon
Name: "{group}\卸载译帧"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-I -X utf8 ""{app}\launch.py"""; WorkingDir: "{app}"; Description: "启动译帧"; Flags: nowait postinstall skipifsilent

[Code]
function GetDefaultDir(Param: String): String;
begin
  if DirExists('D:\') then Result := 'D:\Apps\Yizhen'
  else Result := ExpandConstant('{localappdata}\Programs\Yizhen');
end;
