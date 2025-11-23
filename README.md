### <center>&zwnj;分享DIY实时定位的成果&zwnj;</center>

  需要的设备：
  
    硬件：
      ESP32
      4G模块
      GPS模块
      杜邦线（或其他连接线）
    
    软件：
      thonny
      服务器
      地图api

  我用的是这些：
  
    硬件：
	    ESP32单片机					16元
	    4G模块：FS-MCore-F800系列 	7元
	    GPS:GPS北斗DBS双模模组		16元
    软件：
  	  thonny
  	  华为云
      高德web

剩余供电部分，可自行解决，等我完成时，也会更新。

软件都是免费的，只需要购买硬件部分

高德api申请：https://lbs.amap.com/

把ESP32侧的代码 和 服务器侧的代码 分别运行就可以。

注意修改自己的高德key，服务器代码 224行，替换自己的key。
