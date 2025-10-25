ffmpeg -f lavfi -i "color=c=black:s=1280x720:r=60" -vf "drawbox=x=0:y=abs(mod(t*120*2,2*(ih-2))-(ih-2)):w=iw:h=2:color=white:t=fill,drawbox=x=abs(mod(t*120*2,2*(iw-2))-(iw-2)):y=0:w=2:h=ih:color=red:t=fill" -pix_fmt yuv420p -t 30 test_lines.mp4



curl.exe -f -v -X POST -F 'file=@.\testbars_30_TC.mp4;type=application/octet-stream' "http://192.168.1.167:8080/upload_asset?filename=testbars_30_TC.mp4"
curl.exe -f -v -X POST -F 'file=@.\testbars_30_TC.mp4;type=application/octet-stream' "http://192.168.1.204:8080/upload_asset?filename=testbars_30_TC.mp4"