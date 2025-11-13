using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Runtime.InteropServices;

namespace SetResolution;

internal static class Program
{
    private static int Main(string[] args)
    {
        try
        {
            var options = ResolutionOptions.Parse(args);
            var controller = new DisplayController();
            controller.Log += Console.WriteLine;
            if (!controller.Apply(options))
            {
                Console.Error.WriteLine("Nessuna modalità compatibile trovata per i parametri richiesti.");
                return 2;
            }

            if (controller.CurrentMode is DisplayMode mode)
            {
                var matchesRequest = mode.Width == (uint)options.Width && mode.Height == (uint)options.Height && mode.RefreshHz == (uint)options.RefreshHz;
                var originalColor = Console.ForegroundColor;
                Console.ForegroundColor = matchesRequest ? ConsoleColor.Green : ConsoleColor.Yellow;
                Console.WriteLine($"Risoluzione attuale: {mode.Width}x{mode.Height} @ {mode.RefreshHz}Hz");
                Console.ForegroundColor = originalColor;
                if (!matchesRequest)
                {
                    Console.WriteLine("Il display ha selezionato una modalità differente da quella richiesta.");
                }
            }
            else
            {
                Console.WriteLine("Impossibile determinare la risoluzione attuale del display.");
            }

            if (controller.LastOperationRequiresRestart)
            {
                Console.WriteLine("Modifica applicata, ma potrebbe essere necessario un riavvio per renderla effettiva.");
            }
            Console.WriteLine("Risoluzione applicata con successo.");
            return 0;
        }
        catch (ArgumentException ex)
        {
            Console.Error.WriteLine(ex.Message);
            Console.Error.WriteLine("Uso: SetResolution.exe [larghezza] [altezza] [frequenzaHz]");
            Console.Error.WriteLine("Esempio: SetResolution.exe 1280 720 50");
            return 1;
        }
        catch (Win32Exception ex)
        {
            Console.Error.WriteLine($"Errore Win32: {ex.Message} (0x{ex.NativeErrorCode:X})");
            return ex.NativeErrorCode != 0 ? ex.NativeErrorCode : 3;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Errore inatteso: {ex}");
            return 4;
        }
    }
}

internal sealed record ResolutionOptions(int Width, int Height, int RefreshHz)
{
    public static ResolutionOptions Parse(IReadOnlyList<string> args)
    {
        int width = 1280;
        int height = 720;
        int hz = 50;

        if (args.Count > 0 && !int.TryParse(args[0], out width))
        {
            throw new ArgumentException("Larghezza non valida.");
        }

        if (args.Count > 1 && !int.TryParse(args[1], out height))
        {
            throw new ArgumentException("Altezza non valida.");
        }

        if (args.Count > 2 && !int.TryParse(args[2], out hz))
        {
            throw new ArgumentException("Frequenza non valida.");
        }

        if (width <= 0 || height <= 0 || hz <= 0)
        {
            throw new ArgumentException("I valori devono essere positivi.");
        }

        return new ResolutionOptions(width, height, hz);
    }
}

internal readonly record struct DisplayMode(uint Width, uint Height, uint RefreshHz);

internal sealed class DisplayController
{
    public event Action<string>? Log;

    private const int ENUM_CURRENT_SETTINGS = -1;

    public bool LastOperationRequiresRestart { get; private set; }
    public DisplayMode? CurrentMode { get; private set; }

    public bool Apply(ResolutionOptions options)
    {
        LastOperationRequiresRestart = false;
        CurrentMode = null;

        var modes = EnumerateModes();
        var bestMatch = SelectBestMode(modes, options);
        if (bestMatch is null)
        {
            return false;
        }

        var targetMode = bestMatch.Value;

        Log?.Invoke($"Uso modalità {targetMode.dmPelsWidth}x{targetMode.dmPelsHeight} @ {targetMode.dmDisplayFrequency}Hz");

        targetMode.dmFields = DM.PELSWIDTH | DM.PELSHEIGHT | DM.DISPLAYFREQUENCY | DM.DISPLAYFLAGS | DM.DISPLAYORIENTATION;

        targetMode.dmDisplayFrequency = (uint)options.RefreshHz;
        targetMode.dmPelsWidth = (uint)options.Width;
        targetMode.dmPelsHeight = (uint)options.Height;

        var result = ChangeDisplaySettingsEx(null, ref targetMode, IntPtr.Zero,
            ChangeDisplaySettingsFlags.CDS_UPDATEREGISTRY | ChangeDisplaySettingsFlags.CDS_GLOBAL | ChangeDisplaySettingsFlags.CDS_FULLSCREEN,
            IntPtr.Zero);

        if (result == DISP_CHANGE.Restart)
        {
            LastOperationRequiresRestart = true;
        }
        else if (result != DISP_CHANGE.Successful)
        {
            throw new Win32Exception((int)result, $"ChangeDisplaySettingsEx ha restituito {result}");
        }

        // Applica subito la configurazione salvata nel registro
        result = ChangeDisplaySettingsEx(null, IntPtr.Zero, IntPtr.Zero, ChangeDisplaySettingsFlags.CDS_NONE, IntPtr.Zero);
        if (result == DISP_CHANGE.Restart)
        {
            LastOperationRequiresRestart = true;
        }
        else if (result != DISP_CHANGE.Successful)
        {
            throw new Win32Exception((int)result, $"Rinfresco configurazione ha restituito {result}");
        }

        CurrentMode = TryGetCurrentMode();
        return true;
    }

    private static IReadOnlyList<DEVMODE> EnumerateModes()
    {
        var modes = new List<DEVMODE>();
        var devMode = DEVMODE.Create();
        int modeIndex = 0;
        while (EnumDisplaySettings(null, modeIndex, ref devMode))
        {
            modes.Add(devMode);
            devMode = DEVMODE.Create();
            modeIndex++;
        }

        // Aggiungi impostazioni correnti come fallback
        devMode = DEVMODE.Create();
        if (EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref devMode))
        {
            modes.Add(devMode);
        }
        return modes;
    }

    private static DEVMODE? SelectBestMode(IEnumerable<DEVMODE> modes, ResolutionOptions desired)
    {
        DEVMODE? exact = null;
        foreach (var mode in modes)
        {
            if (mode.dmPelsWidth == desired.Width && mode.dmPelsHeight == desired.Height && mode.dmDisplayFrequency == desired.RefreshHz)
            {
                exact = mode;
                break;
            }
        }

        if (exact is not null)
        {
            return exact;
        }

        // Prova a recuperare stessa risoluzione con frequenza diversa
        DEVMODE? sameResolution = null;
        foreach (var mode in modes.Where(m => m.dmPelsWidth == desired.Width && m.dmPelsHeight == desired.Height))
        {
            if (sameResolution is null ||
                Math.Abs((int)mode.dmDisplayFrequency - desired.RefreshHz) <
                Math.Abs((int)sameResolution.Value.dmDisplayFrequency - desired.RefreshHz))
            {
                sameResolution = mode;
            }
        }

        return sameResolution;
    }

    private static DisplayMode? TryGetCurrentMode()
    {
        var devMode = DEVMODE.Create();
        return EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref devMode)
            ? new DisplayMode(devMode.dmPelsWidth, devMode.dmPelsHeight, devMode.dmDisplayFrequency)
            : null;
    }

    #region P/Invoke

    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    private static extern bool EnumDisplaySettings(string? lpszDeviceName, int iModeNum, ref DEVMODE lpDevMode);

    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    private static extern DISP_CHANGE ChangeDisplaySettingsEx(string? lpszDeviceName, ref DEVMODE lpDevMode, IntPtr hwnd, ChangeDisplaySettingsFlags dwflags, IntPtr lParam);

    [DllImport("user32.dll", CharSet = CharSet.Ansi)]
    private static extern DISP_CHANGE ChangeDisplaySettingsEx(string? lpszDeviceName, IntPtr lpDevMode, IntPtr hwnd, ChangeDisplaySettingsFlags dwflags, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    private struct DEVMODE
    {
        private const int CCHDEVICENAME = 32;
        private const int CCHFORMNAME = 32;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCHDEVICENAME)]
        public string dmDeviceName;

        public ushort dmSpecVersion;
        public ushort dmDriverVersion;
        public ushort dmSize;
        public ushort dmDriverExtra;
        public DM dmFields;

        public POINTL dmPosition;
        public DMDisplayOrientation dmDisplayOrientation;
        public DMDisplayFixedOutput dmDisplayFixedOutput;

        public short dmColor;
        public short dmDuplex;
        public short dmYResolution;
        public short dmTTOption;
        public short dmCollate;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = CCHFORMNAME)]
        public string dmFormName;

        public ushort dmLogPixels;
        public uint dmBitsPerPel;
        public uint dmPelsWidth;
        public uint dmPelsHeight;

        public uint dmDisplayFlags;
        public uint dmDisplayFrequency;
        public uint dmICMMethod;
        public uint dmICMIntent;
        public uint dmMediaType;
        public uint dmDitherType;
        public uint dmReserved1;
        public uint dmReserved2;

        public uint dmPanningWidth;
        public uint dmPanningHeight;

        public static DEVMODE Create()
        {
            var dev = new DEVMODE
            {
                dmDeviceName = new string('\0', CCHDEVICENAME),
                dmFormName = new string('\0', CCHFORMNAME),
                dmSize = (ushort)Marshal.SizeOf<DEVMODE>()
            };
            return dev;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINTL
    {
        public int x;
        public int y;
    }

    [Flags]
    private enum DM : uint
    {
        ORIENTATION = 0x00000001,
        PAPER_SIZE = 0x00000002,
        PAPER_LENGTH = 0x00000004,
        PAPER_WIDTH = 0x00000008,
        POSITION = 0x00000020,
        DISPLAYORIENTATION = 0x00000080,
        COPIES = 0x00000100,
        DEFAULTSOURCE = 0x00000200,
        PRINTQUALITY = 0x00000400,
        COLOR = 0x00000800,
        DUPLEX = 0x00001000,
        YRESOLUTION = 0x00002000,
        TTOPTION = 0x00004000,
        COLLATE = 0x00008000,
        FORMNAME = 0x00010000,
        LOGPIXELS = 0x00020000,
        BITSPERPEL = 0x00040000,
        PELSWIDTH = 0x00080000,
        PELSHEIGHT = 0x00100000,
        DISPLAYFLAGS = 0x00200000,
        DISPLAYFREQUENCY = 0x00400000,
        ICMMETHOD = 0x00800000,
        ICMINTENT = 0x01000000,
        MEDIATYPE = 0x02000000,
        DITHERTYPE = 0x04000000,
        PANNINGWIDTH = 0x08000000,
        PANNINGHEIGHT = 0x10000000,
        DISPLAYFIXEDOUTPUT = 0x20000000
    }

    private enum DMDisplayOrientation : uint
    {
        DMDO_DEFAULT = 0,
        DMDO_90 = 1,
        DMDO_180 = 2,
        DMDO_270 = 3
    }

    private enum DMDisplayFixedOutput : uint
    {
        DMDFO_DEFAULT = 0,
        DMDFO_STRETCH = 1,
        DMDFO_CENTER = 2
    }

    [Flags]
    private enum ChangeDisplaySettingsFlags : uint
    {
        CDS_UPDATEREGISTRY = 0x00000001,
        CDS_TEST = 0x00000002,
        CDS_FULLSCREEN = 0x00000004,
        CDS_GLOBAL = 0x00000008,
        CDS_SET_PRIMARY = 0x00000010,
        CDS_RESET = 0x40000000,
        CDS_NONE = 0x00000000
    }

    private enum DISP_CHANGE : int
    {
        Successful = 0,
        Restart = 1,
        Failed = -1,
        BadMode = -2,
        NotUpdated = -3,
        BadFlags = -4,
        BadParam = -5,
        BadDualView = -6
    }
    #endregion
}
