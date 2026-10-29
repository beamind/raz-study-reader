package com.beamind.smartraz;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(ReaderBackupPlugin.class);
        registerPlugin(ReaderPacksPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
