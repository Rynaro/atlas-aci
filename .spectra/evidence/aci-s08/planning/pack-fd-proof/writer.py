
import pathlib,os,sys,json
r=pathlib.Path(sys.argv[1]);replacement=sys.argv[2]=='replace';data=b'G2-PACK\\n'+b'different-content'*8192+b'G2-END'
(r/'pack-g2').write_bytes(data);os.chmod(r/'pack-g2',0o644);(r/'current.tmp').write_text('pack-g2');os.replace(r/'current.tmp',r/'current');(r/'pack-g1').unlink()
if replacement:
 (r/'replacement.tmp').write_bytes(data);os.chmod(r/'replacement.tmp',0o644);os.replace(r/'replacement.tmp',r/'pack-g1')
print(json.dumps({'published':True,'old_path_exists':(r/'pack-g1').exists()}))
