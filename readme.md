Some Python code for plumbing together a Matrix room and a Minecraft server chat

Setup the app bridge first:
- Drop ServerWrapper.py and the registration.yaml onto your homeserver. 
- Add it to your homeserver config, setup ports and keys and whatnot.
- Run Serverwrapper.py
- On first run, a server config json will be generated. Fill out accordingly and then run again.

Then set up the server wrapper:
- Drop ServerWrapper.py into the folder with your Minecraft .jar. 
- Run once again to generate wrapper.json
- Fill it out.
- Start the Minecraft server, see example below


`python .\ServerWrapper_v0.1.0.py java -Xmx1024M -Xms1024M -jar minecraft_server.1.11.2.jar`
  
The wraper looks in your cwd for anything with "minecraft_server" in the name to know to launch in wrapper mode

This was hacked together over a weekend, so I make no promises. 
