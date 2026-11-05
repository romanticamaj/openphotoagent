; 08 §5: per-user installs go to %LOCALAPPDATA%\Programs. Tauri's default is %LOCALAPPDATA%\OpenPhotoAgent, which is
; the app's data folder (08 §1.5): the bundled pg\ landed inside the data folder's pg\ and an uninstall could reach it.
!macro NSIS_HOOK_PREINSTALL
  ${If} $INSTDIR == "$LOCALAPPDATA\${PRODUCTNAME}"
    StrCpy $INSTDIR "$LOCALAPPDATA\Programs\${PRODUCTNAME}"
    SetOutPath $INSTDIR
  ${EndIf}
!macroend
