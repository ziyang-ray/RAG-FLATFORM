import { Button } from '@/components/ui/button';
import { useLogout } from '@/hooks/use-user-setting-request';
import { LucideUser, LucideLogOut } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';

export function BottomNavbar() {
  const { t } = useTranslation();
  const { logout } = useLogout();

  const handleLogout = () => {
    logout();
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 p-3 flex justify-center items-center gap-4 z-20 shadow-lg">
      <Link 
        to="/admin" 
        className="flex items-center gap-2 px-4 py-2 bg-blue-50 hover:bg-blue-100 text-blue-600 rounded-lg transition-colors"
      >
        <LucideUser className="size-4" />
        <span className="font-medium">{t('common.mpAdmin')}</span>
      </Link>
      
      <Button
        variant="outline"
        size="sm"
        onClick={handleLogout}
        className="flex items-center gap-2 border-red-200 text-red-600 hover:bg-red-50 hover:border-red-300"
      >
        <LucideLogOut className="size-4" />
        <span>{t('common.logout')}</span>
      </Button>
    </div>
  );
}