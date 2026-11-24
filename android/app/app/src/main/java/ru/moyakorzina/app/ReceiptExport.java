package ru.moyakorzina.app;

import android.net.Uri;
import android.os.Handler;
import android.webkit.WebMessage;
import android.webkit.WebMessagePort;
import android.webkit.WebView;
import org.json.JSONObject;
import java.util.UUID;

/** Receives the PDF or JSON download selected in the official receipts window. */
final class ReceiptExport {
    private static final String ORIGIN = "https://lkdr.nalog.ru";
    private static final int MAX_ENCODED = 24 * 1024 * 1024;

    static boolean allowed(String page, String download) {
        if (page == null || download == null || !page.startsWith(ORIGIN + "/")) return false;
        return download.startsWith(ORIGIN + "/") || download.startsWith("blob:" + ORIGIN + "/");
    }

    interface Result { void received(String encoded); }

    static void read(WebView view, Handler handler, String url, Result result) {
        if (!allowed(view.getUrl(), url)) { result.received(null); return; }
        String nonce = UUID.randomUUID().toString();
        WebMessagePort[] ports = view.createWebMessageChannel();
        StringBuilder data = new StringBuilder();
        class Receiver extends WebMessagePort.WebMessageCallback implements Runnable {
            boolean finished;
            void finish(String value) {
                if (finished) return;
                finished = true;
                handler.removeCallbacks(this);
                ports[0].close();
                result.received(value);
            }
            @Override public void run() { finish(null); }
            @Override public void onMessage(WebMessagePort port, WebMessage message) {
                String part = message.getData();
                if (finished) return;
                if ("done".equals(part) && data.length() > 0) finish(data.toString());
                else if (part == null || !part.startsWith(":") || part.length() > 65537 ||
                         data.length() + part.length() - 1 > MAX_ENCODED) finish(null);
                else data.append(part, 1, part.length());
            }
        }
        Receiver receiver = new Receiver();
        ports[0].setWebMessageCallback(receiver);
        handler.postDelayed(receiver, 25000);
        String script = "(function(){if(location.origin!==" + JSONObject.quote(ORIGIN) + ")return false;"
            + "const take=async e=>{if(e.data!==" + JSONObject.quote(nonce) + "||!e.ports[0])return;"
            + "window.removeEventListener('message',take);const port=e.ports[0];try{"
            + "const r=await fetch(" + JSONObject.quote(url) + ",{redirect:'error'});"
            + "if(!r.ok)throw Error();let b=await r.blob();if(!b.size||b.size>18874368)throw Error();"
            + "if(await b.slice(0,5).text()!=='%PDF-'){const fields='receipts ticket document receipt fiscalData fiscalDocument "
            + "key receiptKey date dateTime store retailPlace retailPlaceAddress user userInn total totalSum items "
            + "name price quantity sum paymentType paymentMethodType fiscalDriveNumber fiscalDocumentNumber fiscalSign'.split(' ');"
            + "const json=JSON.stringify(JSON.parse(await b.text()),fields);if(!json.includes('\"items\":['))throw Error();"
            + "b=new Blob([json],{type:'application/json'})}"
            + "const a=new FileReader();a.onerror=()=>port.postMessage('failed');"
            + "a.onload=()=>{const s=a.result.split(',')[1];for(let i=0;i<s.length;i+=65536)"
            + "port.postMessage(':'+s.slice(i,i+65536));port.postMessage('done')};a.readAsDataURL(b)"
            + "}catch{port.postMessage('failed')}};window.addEventListener('message',take);return true})()";
        view.evaluateJavascript(script, ready -> {
            if (!receiver.finished && "true".equals(ready) && allowed(view.getUrl(), url)) {
                view.postWebMessage(new WebMessage(nonce, new WebMessagePort[]{ports[1]}), Uri.parse(ORIGIN));
            } else {
                ports[1].close();
                receiver.finish(null);
            }
        });
    }
}
