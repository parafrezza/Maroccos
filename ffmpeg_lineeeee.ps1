ffmpeg --% ^
 -f lavfi -i "color=c=black:s=1280x720:r=60" ^
 -f lavfi -i "color=c=white:s=1280x2" ^
 -f lavfi -i "color=c=red:s=2x720" ^
 -filter_complex "[0][1]overlay=x=0:y=abs(mod(t*240\,2*(main_h-2))-(main_h-2)):eval=frame[v1]; \
 [v1][2]overlay=x=abs(mod(t*240\,2*(main_w-2))-(main_w-2)):y=0:eval=frame[v2]; \
 [v2]drawtext=fontfile=/Windows/Fonts/consola.ttf: \
 text='%{pts\\:hms}':x=(w-text_w)/2:y=h-text_h-40:fontsize=36:fontcolor=white: \
 box=1:boxcolor=0x00000099" ^
 -pix_fmt yuv420p -t 30 -movflags +faststart out.mp4
